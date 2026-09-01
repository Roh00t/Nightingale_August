"""
Delivery status webhook and the unresolved-delivery view.

The webhook is the ONLY thing that may advance a delivery past `queued`. That
is the whole design: our side of the handoff proves we tried, and the provider's
callback is the only evidence the patient's device received anything.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from services.auth import CallerIdentity, require_roles
from services.supabase_writer import AccessDenied, SupabaseUnavailable
from services.telegram_identity import link_telegram_chat
from services.messaging import (
    DeliveryError,
    STATUS_ORDER,
    apply_provider_status,
    unresolved_for_clinic,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/messaging", tags=["messaging"])


class DeliveryWebhookPayload(BaseModel):
    provider_message_id: str = Field(..., description="Provider's id for the message")
    status: str = Field(..., description=f"One of: {', '.join(STATUS_ORDER)}")
    failure_reason: str | None = None


def _verify_telegram_secret(header_value: str | None) -> None:
    """
    Telegram authenticates its webhook with a shared secret header, not an HMAC.

    `X-Telegram-Bot-Api-Secret-Token` carries whatever string was registered with
    `setWebhook`. It is weaker than a signature — it does not bind to the body,
    so it proves only that the caller knows the secret — but it is what the
    platform sends, and inventing a stronger scheme Telegram will never use
    would mean rejecting every real callback.

    `compare_digest`, not `==`, so the comparison does not leak the secret a byte
    at a time through timing.
    """
    expected = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not expected:
        # Fail closed. This endpoint is unauthenticated by necessity — Telegram
        # holds no JWT — so without the secret there is nothing distinguishing
        # the platform from anyone who learned the URL, and a forged
        # "delivered" is worse than a missing one.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="TELEGRAM_WEBHOOK_SECRET is not configured; refusing unverifiable callbacks.",
        )
    if not header_value or not hmac.compare_digest(expected, header_value):
        logger.warning("Rejected Telegram webhook with a bad or missing secret token")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid secret token")


def _verify_signature(raw_body: bytes, signature: str | None) -> None:
    """
    Reject a webhook that is not from the provider.

    This endpoint is unauthenticated by necessity — a provider cannot hold a
    Supabase JWT — which makes it the one open write path into delivery state.
    Without a signature check, anyone who learns the URL can mark every message
    delivered, and the resulting screen is worse than having no tracking at all:
    staff would trust a green tick that means nothing.

    `compare_digest` rather than `==` so the comparison does not leak the secret
    a byte at a time through timing.
    """
    secret = os.environ.get("MESSAGING_WEBHOOK_SECRET")
    if not secret:
        # Fail closed. An unverifiable webhook is refused rather than trusted,
        # so a deployment that forgets the secret loses status updates instead
        # of silently accepting forged ones.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MESSAGING_WEBHOOK_SECRET is not configured; refusing unverifiable webhooks.",
        )
    if not signature:
        raise HTTPException(status_code=401, detail="Missing signature")

    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        logger.warning("Rejected delivery webhook with a bad signature")
        raise HTTPException(status_code=401, detail="Invalid signature")


@router.post("/delivery-webhook", summary="Provider delivery status callback")
async def delivery_webhook(
    request: Request,
    payload: DeliveryWebhookPayload,
    x_signature: str | None = Header(default=None, alias="X-Signature"),
) -> dict[str, str]:
    _verify_signature(await request.body(), x_signature)

    try:
        updated = apply_provider_status(
            provider_message_id=payload.provider_message_id,
            status=payload.status,
            failure_reason=payload.failure_reason,
        )
    except DeliveryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if updated is None:
        # 200, not 404. Providers retry non-2xx indefinitely, and an unknown id
        # is permanent — retrying it forever buys nothing and buries real
        # callbacks in noise.
        return {"result": "unknown_message"}

    return {"result": "ok", "status": updated["status"]}


@router.get("/unresolved", summary="Deliveries that have not reached the patient")
async def unresolved(
    caller: CallerIdentity = Depends(require_roles("staff", "clinician", "admin")),
) -> dict[str, object]:
    """
    What the front desk needs before a clinic day: everything we believe we sent
    that the patient has not been confirmed to have received.
    """
    rows = unresolved_for_clinic(caller.clinic_id)
    return {"count": len(rows), "deliveries": rows}


class TelegramUpdate(BaseModel):
    """
    One Telegram update. Deliberately permissive.

    Telegram sends dozens of update shapes and adds new ones without notice; a
    strict model would 4xx on an unfamiliar field, Telegram would retry it
    indefinitely, and the retries would bury the callbacks that matter. Unknown
    updates are accepted and ignored.
    """

    model_config = {"extra": "allow"}

    update_id: int | None = None
    message: dict[str, Any] | None = None
    edited_message: dict[str, Any] | None = None


@router.post("/telegram-webhook", summary="Telegram Bot API webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None, alias="X-Telegram-Bot-Api-Secret-Token"
    ),
) -> dict[str, str]:
    """
    Receive Telegram updates: identity binding and delivery signals.

    Always returns 200 once the secret verifies. Telegram retries any non-2xx
    indefinitely, so an update we cannot act on — an unknown token, a message
    that is not `/start` — is acknowledged rather than rejected. Retrying those
    forever buys nothing and drowns the ones that matter.
    """
    _verify_telegram_secret(x_telegram_bot_api_secret_token)

    try:
        payload = await request.json()
    except Exception:
        return {"result": "ignored_unparseable"}

    from services.telegram import parse_start_command

    parsed = parse_start_command(payload if isinstance(payload, dict) else {})
    if parsed is None:
        return {"result": "ignored"}

    chat_id, raw_token = parsed

    try:
        linked = link_telegram_chat(raw_token=raw_token, chat_id=chat_id)
    except AccessDenied:
        # An expired, consumed or unknown token. Answer the same as success:
        # a distinguishable response turns this endpoint into an oracle for
        # guessing tokens, and the person on the other end is told nothing
        # useful either way.
        logger.info("Telegram /start presented a token that could not be redeemed")
        return {"result": "ok"}
    except SupabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    logger.info("Telegram chat linked to profile %s", linked["profile_id"])
    return {"result": "linked"}
