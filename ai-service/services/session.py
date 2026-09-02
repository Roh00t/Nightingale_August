"""
Minting a real Supabase session after phone verification.

WHY NOT SELF-SIGN A JWT.

The shortcut is to sign our own HS256 token with `SUPABASE_JWT_SECRET`. It was
measured against this project and PostgREST accepts it today, because the legacy
symmetric secret is still enabled alongside the ES256 keys the project now
publishes at `/auth/v1/.well-known/jwks.json`. It is still the wrong choice, for
three reasons that all bite later rather than now:

  * **No session to revoke.** A self-signed token exists only in the holder's
    browser. GoTrue has no record of it, so "sign this patient out everywhere"
    and "revoke access for a patient who left the clinic" cannot be implemented
    — the token stays valid until it expires, and nothing can shorten that.
  * **No refresh token.** `@supabase/ssr` maintains a session it can refresh. A
    bare access token drops the patient at a hard logout mid-consultation with
    no way to continue.
  * **It expires as a codebase, not as a token.** Supabase is migrating projects
    off symmetric JWT secrets. When this one flips, every self-minted token stops
    verifying at a date we do not control, and the failure is a total patient
    lockout with no code change to point at.

So this goes through GoTrue's admin API and gets a genuine session: recorded,
refreshable, revocable, and signed with the key the project actually publishes.

THE EMAIL QUESTION, HONESTLY.

GoTrue's magiclink exchange needs the user to carry an email. That looks like
the very thing the OTP flow exists to eliminate, so the distinction has to be
exact. The vulnerability was **staff inventing addresses at the front desk**,
which produced three failures: a patient who cannot log in, a record asserting
they can, and collisions when two clinics both reach for `patient1@`.

A system-generated sentinel at a reserved non-routable domain has none of those
properties. It is never typed by a human, never shown to staff, never a login
factor, never a delivery channel, and unique by construction. The phone remains
the credential; this is an internal primary key that happens to be shaped like an
email because GoTrue requires one.

What it is NOT: a claim that the patient is reachable there. Nothing in this
system may send to it, which is why the domain is `.invalid` — reserved by
RFC 2606 precisely so it can never resolve.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# RFC 2606 reserves .invalid. A message addressed here cannot be delivered by
# any conforming resolver, which is the point: the address must be structurally
# incapable of being a communication channel.
SENTINEL_EMAIL_DOMAIN = "patient.nightingale.invalid"

# Only ever mint for a patient. If a clinician's phone were in `profiles`, an
# OTP to their handset would be a password-free path into a clinical account —
# turning a convenience for patients into privilege escalation for anyone who
# controls a staff phone number for sixty seconds.
MINTABLE_ROLES = frozenset({"patient"})


class SessionMintError(RuntimeError):
    """Session could not be created. Never carries provider detail to the caller."""


# Async throughout. These are called from async FastAPI endpoints, and the
# synchronous httpx.Client they used before blocked the event loop for the
# duration of two 20-second requests — up to 40 seconds during which every other
# request, including /health, queued behind one sign-in. Under a clinic's morning
# login rush that serialises the whole service.
#
# Refactored to AsyncClient rather than wrapped in run_in_threadpool: these are
# pure network I/O with no CPU-bound or thread-affine work, so async is the shape
# that actually fits. A threadpool would have hidden the blocking rather than
# removed it.


@dataclass
class MintedSession:
    access_token: str
    refresh_token: str
    expires_in: int
    user_id: str


def sentinel_email(profile_id: str) -> str:
    """
    Deterministic internal address for a profile.

    Derived from the profile id, so it is stable across re-provisioning and
    cannot collide. Deterministic rather than random specifically so a repeated
    provision finds the existing user instead of creating a second account for
    the same patient.
    """
    return f"p-{profile_id}@{SENTINEL_EMAIL_DOMAIN}"


def _admin_headers() -> dict[str, str]:
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise SessionMintError("SUPABASE_SERVICE_ROLE_KEY is not configured")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    if not url:
        raise SessionMintError("SUPABASE_URL is not configured")
    return url.rstrip("/")


async def ensure_auth_user(*, profile_id: str, phone: str) -> str:
    """
    Make sure an `auth.users` row exists for this profile, and return its id.

    Idempotent. `phone_confirm` is set because we have just verified possession
    of the handset ourselves — recording it as unconfirmed would misstate what
    the clinic knows, and GoTrue would then be entitled to challenge it again.

    `email_confirm` is also set, which reads alarmingly until you note the
    address is non-routable: leaving it unconfirmed would park the account in a
    pending state waiting for a confirmation email that can never arrive.
    """
    email = sentinel_email(profile_id)
    base, headers = _base_url(), _admin_headers()

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{base}/auth/v1/admin/users",
            headers=headers,
            json={
                "email": email,
                "phone": phone,
                "email_confirm": True,
                "phone_confirm": True,
                # The profile row is the source of truth for role and clinic;
                # this is a back-reference for support, not an authorisation
                # claim. Nothing reads it to make a decision.
                "user_metadata": {"profile_id": profile_id, "identity": "phone_otp"},
            },
        )

        if resp.status_code in (200, 201):
            return resp.json()["id"]

        # Already provisioned. GoTrue answers 422 for a duplicate; look the user
        # up rather than treating it as an error, so a returning patient signs in
        # instead of being told their account exists.
        if resp.status_code in (400, 409, 422):
            found = await client.get(
                f"{base}/auth/v1/admin/users",
                headers=headers,
                params={"filter": email},
            )
            if found.status_code == 200:
                for user in (found.json().get("users") or []):
                    if user.get("email") == email:
                        return user["id"]

        logger.error(
            "Could not provision auth user for profile %s: %s", profile_id, resp.status_code
        )
        raise SessionMintError("Could not provision an account for this patient")


async def mint_session(*, profile_id: str, phone: str, role: str) -> MintedSession:
    """
    Exchange a verified phone for a real GoTrue session.

    Two admin calls: generate a single-use magiclink token, then redeem it. The
    link is never sent anywhere — it is created and immediately consumed
    server-side, so the token has no window in which it exists outside this
    function.

    Callers MUST have verified the OTP first. This function does not re-check,
    because it has nothing to check against; it is the router's job, and the
    router is the only caller.
    """
    if role not in MINTABLE_ROLES:
        # Defence in depth. `_lookup_profile_by_phone` already filters to
        # patients, but a future caller that forgets would otherwise turn an SMS
        # into a staff login.
        logger.error("Refused to mint a session for role %r", role)
        raise SessionMintError("Sessions may only be minted for patient accounts")

    user_id = await ensure_auth_user(profile_id=profile_id, phone=phone)
    email = sentinel_email(profile_id)
    base, headers = _base_url(), _admin_headers()

    anon = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not anon:
        raise SessionMintError("Anon key is not configured")

    async with httpx.AsyncClient(timeout=20.0) as client:
        link = await client.post(
            f"{base}/auth/v1/admin/generate_link",
            headers=headers,
            json={"type": "magiclink", "email": email},
        )
        if link.status_code != 200:
            logger.error("generate_link failed: %s", link.status_code)
            raise SessionMintError("Could not start a session for this patient")

        hashed_token = link.json().get("hashed_token")
        if not hashed_token:
            raise SessionMintError("Session provider returned no token")

        # Redeemed with the ANON key, not the service key. The exchange must
        # happen as an ordinary client would perform it, so the session comes
        # back scoped to the user rather than inheriting service-role authority.
        session = await client.post(
            f"{base}/auth/v1/verify",
            headers={"apikey": anon, "Content-Type": "application/json"},
            # `token_hash`, not `token`. GoTrue treats `token` as the plaintext
            # OTP and then demands an accompanying email or phone, rejecting the
            # request with "Only an email address or phone number should be
            # provided on verify". The hashed form is the one an admin-generated
            # link carries. Caught by running the exchange, not by reading it.
            json={"type": "magiclink", "token_hash": hashed_token},
        )
        if session.status_code != 200:
            logger.error("Session exchange failed: %s", session.status_code)
            raise SessionMintError("Could not complete sign-in")

        body: dict[str, Any] = session.json()

    access, refresh = body.get("access_token"), body.get("refresh_token")
    if not access or not refresh:
        raise SessionMintError("Session provider returned an incomplete session")

    logger.info("Minted patient session for profile %s", profile_id)
    return MintedSession(
        access_token=access,
        refresh_token=refresh,
        expires_in=int(body.get("expires_in") or 3600),
        user_id=user_id,
    )
