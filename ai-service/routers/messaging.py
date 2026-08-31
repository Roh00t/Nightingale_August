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

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from services.auth import CallerIdentity, require_roles
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
