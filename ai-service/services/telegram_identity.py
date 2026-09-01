"""
Passwordless patient identity via an access token.

Serves two entry points that redeem the same token:

  * a Telegram `/start <token>` deep link, which also binds the chat_id
  * a browser `/portal/login?token=<raw>` link, for a patient without Telegram

Both exist because "reachable on WhatsApp" does not mean "has Telegram", and a
clinic that can only onboard one kind of patient has not solved the problem.

WHAT IS STORED. Only a SHA-256 hash of the token. The table is readable by clinic
staff for support, and a plaintext token sitting in a support view is a
credential lying in the open — anyone who can read the row can become the
patient. The plaintext exists in exactly one place: the link that was handed
over.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from services.supabase_writer import AccessDenied, get_service_client

logger = logging.getLogger(__name__)

# Telegram's start parameter permits only [A-Za-z0-9_-] and at most 64
# characters, so tokens are generated in that alphabet. token_urlsafe uses
# exactly this alphabet, which is why it is used rather than token_hex — the
# same token has to survive both a URL query string and a Telegram deep link.
TOKEN_BYTES = 32

# Portal links are longer-lived than an OTP because they are a replacement for
# an account, not a second factor: a patient may open the message hours later.
# Still bounded — a link that reaches a recycled number is a standing exposure
# for as long as it is valid.
DEFAULT_TTL = timedelta(hours=72)

MAX_FAILED_ATTEMPTS = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(raw: str) -> str:
    """
    SHA-256 of the raw token.

    Unpeppered, unlike the OTP hash, and the difference is deliberate: an OTP is
    six digits — a 10^6 space a leaked database could brute-force offline, so it
    needs a server-side secret. This token carries 256 bits of entropy from
    `secrets`, which is not brute-forceable whether or not the hash is peppered.
    Adding a pepper here would buy nothing and would make tokens unverifiable if
    the pepper were ever rotated.
    """
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_access_token(
    *,
    profile_id: str,
    clinic_id: str,
    purpose: str = "portal_access",
    ttl: timedelta = DEFAULT_TTL,
    created_by: str | None = None,
    max_uses: int = 1,
) -> tuple[str, dict[str, Any]]:
    """
    Mint a token and store only its hash. Returns (raw_token, row).

    The raw token is returned once, to the caller who will put it in a link. It
    is never logged and never stored.
    """
    raw = secrets.token_urlsafe(TOKEN_BYTES)[:64]
    client = get_service_client()
    resp = (
        client.table("patient_access_tokens")
        .insert({
            "profile_id": profile_id,
            "clinic_id": clinic_id,
            "token_hash": hash_token(raw),
            "purpose": purpose,
            "expires_at": (_now() + ttl).isoformat(),
            "max_uses": max_uses,
            "created_by": created_by,
        })
        .execute()
    )
    if not resp.data:
        raise AccessDenied("Could not issue an access token")

    logger.info("Access token issued token_id=%s profile=%s", resp.data[0]["id"], profile_id)
    return raw, resp.data[0]


def _load_redeemable(raw_token: str) -> dict[str, Any]:
    """
    Fetch a token row that is currently redeemable, or raise.

    Every rejection raises the SAME exception with the same message. Expired,
    consumed, unknown and attempt-exhausted must be indistinguishable: each
    distinction is an oracle, and "unknown" in particular would let someone
    enumerate valid tokens by timing the difference between a miss and a hit.
    """
    denied = AccessDenied("That link is no longer valid. Ask the clinic for a new one.")

    if not raw_token or len(raw_token) < 16:
        raise denied

    client = get_service_client()
    rows = (
        client.table("patient_access_tokens")
        .select("*")
        .eq("token_hash", hash_token(raw_token))
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise denied

    token = rows[0]

    if (token.get("failed_attempts") or 0) >= MAX_FAILED_ATTEMPTS:
        raise denied
    if (token.get("use_count") or 0) >= (token.get("max_uses") or 1):
        raise denied
    if token.get("consumed_at") is not None and (token.get("max_uses") or 1) <= 1:
        raise denied

    expires_at = token["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    if expires_at <= _now():
        raise denied

    return token


def _consume(token: dict[str, Any]) -> dict[str, Any]:
    """
    Advance the use counter, conditioned on its current value.

    The `.eq("use_count", ...)` is a compare-and-swap. Two requests racing with
    the same link must not both succeed — the second matches no row, because the
    first already moved the counter.
    """
    client = get_service_client()
    current = token.get("use_count") or 0
    updated = (
        client.table("patient_access_tokens")
        .update({"use_count": current + 1, "consumed_at": _now().isoformat()})
        .eq("id", token["id"])
        .eq("use_count", current)
        .execute()
    ).data or []
    if not updated:
        raise AccessDenied("That link is no longer valid. Ask the clinic for a new one.")
    return updated[0]


def redeem_token(raw_token: str) -> dict[str, Any]:
    """
    Redeem a token for the identity it belongs to. Consumes it.

    Returns the profile fields a caller needs to mint a session. Does NOT mint
    one itself — that is `services/session.py`, and keeping them separate means
    this can also be used for a link that grants a one-off view without a
    full login.
    """
    token = _load_redeemable(raw_token)
    _consume(token)

    client = get_service_client()
    rows = (
        client.table("profiles")
        .select("id, clinic_id, role, display_name, phone_e164")
        .eq("id", token["profile_id"])
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise AccessDenied("That link is no longer valid. Ask the clinic for a new one.")

    profile = rows[0]
    if profile.get("role") != "patient":
        # A token minted against a staff profile would be a password-free path
        # into a clinical account. Refused regardless of how it was created.
        logger.error("Refused token redemption for non-patient role %r", profile.get("role"))
        raise AccessDenied("That link is no longer valid. Ask the clinic for a new one.")

    logger.info("Access token redeemed for profile %s", profile["id"])
    return profile


def link_telegram_chat(*, raw_token: str, chat_id: int) -> dict[str, Any]:
    """
    Bind a Telegram chat to the profile a token belongs to.

    This is the only way a chat_id enters the system. Telegram cannot be asked
    "what is the chat for this phone number" — the patient has to open the bot,
    and that tap is the consent.
    """
    profile = redeem_token(raw_token)

    client = get_service_client()
    updated = (
        client.table("profiles")
        .update({"telegram_chat_id": chat_id, "telegram_linked_at": _now().isoformat()})
        .eq("id", profile["id"])
        .execute()
    ).data or []
    if not updated:
        raise AccessDenied("Could not link that chat")

    return {"profile_id": profile["id"], "clinic_id": profile["clinic_id"], "chat_id": chat_id}


def record_failed_attempt(raw_token: str) -> None:
    """Increment the attempt counter for a token that exists but was refused."""
    client = get_service_client()
    rows = (
        client.table("patient_access_tokens")
        .select("id, failed_attempts")
        .eq("token_hash", hash_token(raw_token))
        .limit(1)
        .execute()
    ).data or []
    if rows:
        client.table("patient_access_tokens").update(
            {"failed_attempts": (rows[0].get("failed_attempts") or 0) + 1}
        ).eq("id", rows[0]["id"]).execute()
