"""
Passwordless patient access: token redemption and front-desk link generation.

Removes the motive for the workaround this system was built around. `auth.users`
requires an email, so a front desk faced with a patient who has none invents one
— `patient1@clinic.local` — producing a patient who cannot log in, a record
claiming they can, and collisions when two clinics reach for the same string.
A phone-and-link path removes the requirement, and so removes the invention.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from services.auth import CallerIdentity, require_roles
from services.session import SessionMintError, mint_session
from services.supabase_writer import AccessDenied, SupabaseUnavailable
from services.telegram_identity import issue_access_token, redeem_token, record_failed_attempt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RedeemTokenRequest(BaseModel):
    token: str = Field(..., min_length=16, max_length=64)


class RedeemTokenResponse(BaseModel):
    profile_id: str
    clinic_id: str
    access_token: str
    refresh_token: str
    expires_in: int


@router.post("/redeem-token", response_model=RedeemTokenResponse)
async def redeem_access_token(request: RedeemTokenRequest) -> RedeemTokenResponse:
    """
    Exchange a raw access token for a patient session.

    Unauthenticated by necessity — the whole point is a patient with no account —
    which makes it one of the most exposed surfaces here. Every failure returns
    the same 401 with the same wording: expired, consumed, unknown and
    attempt-exhausted must be indistinguishable, because each distinction is an
    oracle and "unknown" is the one that lets someone probe for valid tokens.
    """
    generic = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="That link is no longer valid. Ask the clinic for a new one.",
    )

    try:
        profile = redeem_token(request.token)
    except AccessDenied:
        # Counted against the token so a guessing loop exhausts it rather than
        # running indefinitely. Best-effort: a token that does not exist has no
        # row to count against, which is what the length floor and 256 bits of
        # entropy are for.
        try:
            record_failed_attempt(request.token)
        except Exception:
            logger.debug("Could not record a failed redemption attempt", exc_info=True)
        raise generic
    except SupabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        session = await mint_session(
            profile_id=profile["id"],
            phone=profile.get("phone_e164") or "",
            role=profile["role"],
        )
    except SessionMintError as exc:
        logger.error("Session mint failed after a valid token: %s", exc)
        # 503, not 401. The patient did everything right; sending them round the
        # loop with a fresh link would fail the same way.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Signed in, but the session could not be created. Try again shortly.",
        )

    return RedeemTokenResponse(
        profile_id=profile["id"],
        clinic_id=profile["clinic_id"],
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_in=session.expires_in,
    )


class PatientLinkRequest(BaseModel):
    profile_id: str
    purpose: str = Field(default="portal_access", pattern="^(portal_access|appointment)$")


class PatientLinkResponse(BaseModel):
    telegram_link: str | None
    portal_link: str
    expires_at: str
    warning: str | None = None


@router.post("/patient-link", response_model=PatientLinkResponse)
async def create_patient_link(
    request: PatientLinkRequest,
    caller: CallerIdentity = Depends(require_roles("staff", "clinician", "admin")),
) -> PatientLinkResponse:
    """
    Front-desk helper: mint a token and return the links to hand the patient.

    Returns BOTH forms on purpose. "Reachable on WhatsApp" does not imply "has
    Telegram", and a clinic that can only onboard one kind of patient has not
    solved the problem — the portal link works in any browser, including one
    opened from a WhatsApp message.

    Staff may generate these; the token grants a *patient* session and
    `redeem_token` refuses any profile whose role is not `patient`, so this
    cannot be turned into a path into a colleague's account.
    """
    from services.telegram import TelegramNotConfigured, start_link

    try:
        raw, row = issue_access_token(
            profile_id=request.profile_id,
            clinic_id=caller.clinic_id,
            purpose=request.purpose,
            created_by=caller.user_id,
        )
    except AccessDenied as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SupabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    import os

    portal_base = os.environ.get("PATIENT_PORTAL_URL", "http://localhost:3000").rstrip("/")
    portal_link = f"{portal_base}/portal/login?token={raw}"

    telegram_link: str | None = None
    warning: str | None = None
    try:
        telegram_link = start_link(raw)
    except TelegramNotConfigured:
        # Not an error. A deployment without a bot still hands out portal links;
        # saying so beats returning a link that goes nowhere.
        warning = "Telegram is not configured on this deployment; use the portal link."

    # The raw token is in the response because the caller has to put it in a
    # link. It is deliberately NOT logged anywhere — the log records the token
    # id, which identifies the issuance without being usable as one.
    return PatientLinkResponse(
        telegram_link=telegram_link,
        portal_link=portal_link,
        expires_at=str(row["expires_at"]),
        warning=warning,
    )
