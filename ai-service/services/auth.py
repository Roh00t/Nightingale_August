"""
JWT verification for AI service endpoints.

Guardrail S6: every AI endpoint authenticates. CORS is a browser-side control
and does nothing against a direct request, so before this existed anyone able to
reach port 8000 could submit arbitrary clinical text and spend the Groq quota.

Verification FAILS CLOSED. If no verification key is configured the endpoints
return 503 rather than accepting unverified callers — an unconfigured service
must not be an open one.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


@dataclass
class CallerIdentity:
    """An authenticated caller, resolved from the JWT and their profile."""

    user_id: str
    role: str
    clinic_id: str
    display_name: str


def _verification_key() -> tuple[object, list[str]]:
    """
    Resolve the Supabase JWT verification key.

    Prefers ES256 via SUPABASE_JWT_JWK (current Supabase projects use asymmetric
    signing keys); falls back to the legacy HS256 shared secret.
    """
    jwk_json = os.environ.get("SUPABASE_JWT_JWK")
    if jwk_json:
        from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey

        key: EllipticCurvePublicKey = jwt.algorithms.ECAlgorithm.from_jwk(  # type: ignore[assignment]
            json.dumps(json.loads(jwk_json))
        )
        return key, ["ES256"]

    secret = os.environ.get("SUPABASE_JWT_SECRET")
    if secret:
        return secret, ["HS256"]

    raise RuntimeError(
        "Neither SUPABASE_JWT_JWK nor SUPABASE_JWT_SECRET is set; "
        "AI endpoints cannot verify callers"
    )


async def require_caller(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CallerIdentity:
    """
    FastAPI dependency: verify the bearer token and resolve the caller's profile.

    Raises 401 for a missing or invalid token, 403 when the token is valid but
    has no profile, and 503 when the service itself is not configured to verify.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        key, algorithms = _verification_key()
    except RuntimeError as exc:
        logger.error("JWT verification unavailable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured on this service",
        ) from exc

    try:
        payload = jwt.decode(
            credentials.credentials,
            key,
            algorithms=algorithms,
            audience="authenticated",
            options={"verify_aud": False},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        ) from exc

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has no subject"
        )

    from services.supabase_writer import SupabaseUnavailable, get_profile

    try:
        profile = get_profile(user_id)
    except SupabaseUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No profile for this user"
        )

    return CallerIdentity(
        user_id=user_id,
        role=profile["role"],
        clinic_id=profile["clinic_id"],
        display_name=profile.get("display_name", ""),
    )


def require_roles(*allowed: str):
    """Dependency factory restricting an endpoint to specific roles."""

    async def _check(caller: CallerIdentity = Depends(require_caller)) -> CallerIdentity:
        if caller.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{caller.role}' may not use this endpoint",
            )
        return caller

    return _check
