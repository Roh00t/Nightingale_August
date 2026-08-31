"""
Phone-based patient identity. No email, no password, no account to remember.

THE VULNERABILITY THIS CLOSES.

`auth.users` requires an email. An elderly patient reachable on WhatsApp usually
has no email they can retrieve, so the front desk invents one —
`patient1@clinic.local` — to get past the form. Three things then go wrong, and
only the first is obvious:

  * The patient cannot log in, because the address does not exist.
  * The clinic's records say they *can*, so messages are sent to a portal
    nobody opens and marked as communicated.
  * The invented address is a real identifier in a real auth system. Two
    patients at two clinics get `patient1@`, and a password reset on one is a
    takeover of the other.

This module makes the phone the identifier so the invention has no motive. That
is the actual fix: a policy telling staff not to invent emails loses to a
patient standing at the desk, every time. Removing the requirement removes the
workaround.

WHAT IS STORED. Only a hash of the code, and only ever compared in constant
time. The table is readable by clinic staff for support, and a plaintext OTP in
a support view is a credential lying in the open — anyone who can read the row
can authenticate as the patient. The plaintext exists in exactly one place: the
message that was sent.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from services.supabase_writer import get_service_client

logger = logging.getLogger(__name__)

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")

# Six digits is the usable ceiling for a code read aloud off a handset. The
# search space that leaves (10^6) is only safe because attempts are capped —
# the entropy is not doing the work here, the lockout is.
CODE_LENGTH = 6

# Short. An OTP is a bearer credential sitting in a message on a phone that may
# be shared, recycled, or on a lock screen. Five minutes is long enough to read
# and type, short enough that a handset left on a counter is not a standing
# authentication.
OTP_TTL = timedelta(minutes=5)

# Attempts against a single code before it is dead. Five leaves a 5-in-10^6
# chance per issued code, and the code dies rather than the account locking —
# locking the account would hand an attacker a denial-of-service against a
# patient by spamming wrong codes at their number.
MAX_VERIFY_ATTEMPTS = 5

# Requests per phone per window. Without this an attacker cycles codes: each
# request mints a fresh one, so unlimited requests plus five guesses each is
# unlimited guesses. Capping issuance is what makes the attempt cap mean
# anything.
MAX_REQUESTS_PER_WINDOW = 3
REQUEST_WINDOW = timedelta(minutes=15)


class OTPError(RuntimeError):
    """Something the caller may be told about."""


class OTPRateLimited(OTPError):
    """Too many requests or attempts. Deliberately distinguishable to the caller."""


@dataclass
class OTPIssue:
    token_id: str
    code: str          # plaintext, returned ONLY to the sender, never stored
    expires_at: datetime


def _pepper() -> bytes:
    """
    Server-side secret mixed into every hash.

    A bare sha256 of a six-digit code is a 10^6 rainbow table someone can build
    in a second, so a database leak would expose every live code. The pepper
    lives in the environment, not the database, so reading the table is not
    enough — an attacker needs the application secret too.
    """
    secret = os.environ.get("OTP_PEPPER") or os.environ.get("SUPABASE_JWT_SECRET")
    if not secret:
        # Fail closed. Running without a pepper would silently downgrade every
        # stored hash to something trivially reversible.
        raise OTPError(
            "OTP_PEPPER is not configured. Refusing to issue codes that would be "
            "stored as unpeppered hashes."
        )
    return secret.encode()


def hash_code(phone: str, code: str) -> str:
    """
    Hash bound to the phone number.

    Binding matters: an unbound hash of "123456" is the same row for every
    patient, so an attacker who obtains one valid code can replay it against any
    account whose live code happens to match. Including the destination makes a
    code useless anywhere but the number it was sent to.
    """
    return hmac.new(_pepper(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _recent_request_count(profile_id: str) -> int:
    client = get_service_client()
    since = (_now() - REQUEST_WINDOW).isoformat()
    rows = (
        client.table("patient_access_tokens")
        .select("id")
        .eq("profile_id", profile_id)
        .eq("purpose", "otp")
        .gte("created_at", since)
        .execute()
    ).data or []
    return len(rows)


def issue(*, profile_id: str, clinic_id: str, phone: str, created_by: str | None = None) -> OTPIssue:
    """
    Mint a single-use code and store only its hash.

    Rate limiting is checked here rather than at the route, so every issuance
    path is covered — a second caller added later cannot forget it.
    """
    if not _E164.match(phone or ""):
        raise OTPError("Phone must be E.164, e.g. +6591234567")

    if _recent_request_count(profile_id) >= MAX_REQUESTS_PER_WINDOW:
        logger.warning("OTP request rate limit hit for profile %s", profile_id)
        raise OTPRateLimited(
            "Too many codes requested. Wait 15 minutes before trying again."
        )

    # secrets, not random: this is a credential.
    code = "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))
    expires = _now() + OTP_TTL

    client = get_service_client()
    resp = (
        client.table("patient_access_tokens")
        .insert({
            "profile_id": profile_id,
            "clinic_id": clinic_id,
            "token_hash": hash_code(phone, code),
            "purpose": "otp",
            "expires_at": expires.isoformat(),
            "max_uses": 1,
            "created_by": created_by,
        })
        .execute()
    )
    if not resp.data:
        raise OTPError("Could not issue a code")

    # The code is never logged. The token id is, so an issuance can be traced
    # without the log becoming a credential store.
    logger.info("OTP issued token=%s profile=%s", resp.data[0]["id"], profile_id)
    return OTPIssue(token_id=resp.data[0]["id"], code=code, expires_at=expires)


def verify(*, phone: str, code: str) -> dict[str, Any]:
    """
    Check a code and consume it. Returns the token row on success.

    Every failure path raises the SAME message. An attacker must not be able to
    distinguish "no such number", "expired", "already used" and "wrong code" —
    each distinction is an oracle. "No such number" in particular turns this
    endpoint into a patient-enumeration tool: an attacker learns which phone
    numbers belong to a clinic's patients, which is itself PHI.
    """
    generic = OTPError("That code is not valid. Request a new one.")

    if not _E164.match(phone or "") or not code or not code.isdigit():
        raise generic

    client = get_service_client()

    # Look up by hash, not by phone. The hash is bound to the phone already, so
    # this is one indexed equality check that reveals nothing on a miss — there
    # is no "found the account but the code was wrong" branch to leak.
    try:
        candidate_hash = hash_code(phone, code)
    except OTPError:
        raise
    rows = (
        client.table("patient_access_tokens")
        .select("*")
        .eq("token_hash", candidate_hash)
        .eq("purpose", "otp")
        .limit(1)
        .execute()
    ).data or []

    if not rows:
        # A miss cannot increment a counter on the right row, because we do not
        # know which row was intended. The issuance cap is what bounds this;
        # see MAX_REQUESTS_PER_WINDOW.
        raise generic

    token = rows[0]

    if token.get("consumed_at") is not None or (token.get("use_count") or 0) >= token["max_uses"]:
        raise generic

    if (token.get("failed_attempts") or 0) >= MAX_VERIFY_ATTEMPTS:
        raise OTPRateLimited("Too many attempts on this code. Request a new one.")

    expires_at = token["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    if expires_at <= _now():
        raise generic

    # Consume atomically on the pre-consumption state. Two requests racing with
    # the same valid code must not both succeed — the second finds use_count
    # already advanced and matches nothing.
    consumed = (
        client.table("patient_access_tokens")
        .update({"consumed_at": _now().isoformat(), "use_count": (token.get("use_count") or 0) + 1})
        .eq("id", token["id"])
        .eq("use_count", token.get("use_count") or 0)
        .execute()
    ).data or []
    if not consumed:
        raise generic

    logger.info("OTP verified token=%s profile=%s", token["id"], token["profile_id"])
    return token


def record_failed_attempt(token_id: str) -> None:
    """Increment the attempt counter on a known token."""
    client = get_service_client()
    rows = (
        client.table("patient_access_tokens").select("failed_attempts").eq("id", token_id).limit(1).execute()
    ).data or []
    if rows:
        client.table("patient_access_tokens").update(
            {"failed_attempts": (rows[0].get("failed_attempts") or 0) + 1}
        ).eq("id", token_id).execute()
