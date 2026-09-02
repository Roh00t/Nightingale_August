"""
Phone-based patient authentication.

`POST /api/auth/request-otp` and `POST /api/auth/verify-otp`. Both are
unauthenticated by necessity — a patient signing in has no session yet — which
makes them the most exposed surface in the service and the reason nearly every
decision here is about what NOT to reveal.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from services import otp
from services.messaging import DeliveryError, queue_delivery
from services.session import SessionMintError, mint_session
from services.supabase_writer import get_service_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RequestOTPRequest(BaseModel):
    phone: str = Field(..., description="E.164, e.g. +6591234567")


class RequestOTPResponse(BaseModel):
    # No token id, no expiry, no "we found you". See the docstring below.
    status: str = "accepted"
    message: str


class VerifyOTPRequest(BaseModel):
    phone: str
    code: str = Field(..., min_length=4, max_length=10)


class VerifyOTPResponse(BaseModel):
    profile_id: str
    clinic_id: str
    verified: bool = True
    # A real GoTrue session: recorded, refreshable, revocable. The client hands
    # these to supabase.auth.setSession() and is signed in exactly as if it had
    # used a password.
    access_token: str
    refresh_token: str
    expires_in: int


def _lookup_profile_by_phone(phone: str) -> dict | None:
    client = get_service_client()
    rows = (
        client.table("profiles")
        .select("id, clinic_id, role, phone_e164")
        .eq("phone_e164", phone)
        .eq("role", "patient")
        .limit(1)
        .execute()
    ).data or []
    return rows[0] if rows else None


@router.post("/request-otp", response_model=RequestOTPResponse)
async def request_otp(request: RequestOTPRequest) -> RequestOTPResponse:
    """
    Send a one-time code to a patient's phone.

    ALWAYS returns the same response, whether or not the number belongs to a
    patient. The obvious implementation — 404 for an unknown number — turns this
    into a patient-enumeration oracle: an attacker walks the SG mobile range and
    learns which numbers belong to this clinic's patients. That membership is
    itself PHI, and it is disclosed before anyone authenticates.

    So an unknown number is accepted, no code is issued, and nothing is sent.
    The caller cannot tell the difference. The timing difference between the two
    paths is real and not defended here — closing it would need a constant-time
    response budget, which is noted rather than claimed.
    """
    profile = _lookup_profile_by_phone(request.phone)

    generic = RequestOTPResponse(
        message="If that number is registered, a code has been sent to it."
    )

    if profile is None:
        logger.info("OTP requested for an unregistered number")
        return generic

    try:
        issued = otp.issue(
            profile_id=profile["id"],
            clinic_id=profile["clinic_id"],
            phone=request.phone,
        )
    except otp.OTPRateLimited:
        # Rate limiting is surfaced. Hiding it behind the generic response would
        # leave a legitimate patient retyping a number that will never work,
        # and the fact that *some* limit exists is not a useful secret — the
        # attacker learns it from being blocked regardless.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many codes requested for that number. Wait 15 minutes.",
        )
    except otp.OTPError as exc:
        logger.error("OTP issuance failed: %s", exc)
        # Deliberately generic to the caller: an issuance fault must not tell an
        # attacker that the number exists.
        return generic

    try:
        # Sent through the traced path, so an OTP that never arrives is visible
        # as an unresolved delivery rather than as a patient who "did not try".
        await queue_delivery(
            clinic_id=profile["clinic_id"],
            profile_id=profile["id"],
            channel="whatsapp",
            destination=request.phone,
            token_id=issued.token_id,
            body=f"Your clinic access code is {issued.code}. It expires in 5 minutes.",
        )
    except DeliveryError as exc:
        logger.error("OTP delivery could not be queued: %s", exc)

    return generic


@router.post("/verify-otp", response_model=VerifyOTPResponse)
async def verify_otp(request: VerifyOTPRequest) -> VerifyOTPResponse:
    """
    Check a code and consume it.

    Returns the verified identity. It does NOT mint a Supabase session — see the
    note below, which is a real boundary rather than an omission.
    """
    try:
        token = otp.verify(phone=request.phone, code=request.code)
    except otp.OTPRateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except otp.OTPError:
        # One message for every failure mode. "Expired" vs "wrong" vs "no such
        # number" are each an oracle, and the last one is patient enumeration.
        raise HTTPException(status_code=401, detail="That code is not valid. Request a new one.")

    # The code is already consumed at this point, whatever happens next. That
    # ordering is deliberate: a code that survives a failed session mint could be
    # replayed, and re-using a one-time credential is worse than making the
    # patient request a new one.
    profile = _lookup_profile_by_phone(request.phone)
    if profile is None or profile["id"] != token["profile_id"]:
        # The phone moved to a different profile between issue and verify, or the
        # profile was deleted. Refuse rather than guess which patient this is.
        logger.error("Profile mismatch on OTP verify for token %s", token["id"])
        raise HTTPException(status_code=401, detail="That code is not valid. Request a new one.")

    try:
        session = await mint_session(
            profile_id=profile["id"], phone=request.phone, role=profile["role"]
        )
    except SessionMintError as exc:
        logger.error("Session mint failed after valid OTP: %s", exc)
        # 503, not 401. The patient did everything right and the failure is ours;
        # a 401 would send them round the loop entering codes that will keep
        # working and keep failing.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Signed in, but the session could not be created. Try again shortly.",
        )

    return VerifyOTPResponse(
        profile_id=token["profile_id"],
        clinic_id=token["clinic_id"],
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_in=session.expires_in,
    )
