"""
Sliding-window rate limiting, in-process, no new dependency.

WHY NOT slowapi. It is not installed, and adding a dependency 36 hours before a
deadline means a Railway rebuild on a path nobody has exercised. This is ~120
lines with no install risk. If the service later runs more than one replica,
replace it with a Redis-backed limiter rather than raising the numbers.

WHAT THIS DOES AND DOES NOT PROTECT.

It bounds requests **per process**. On a single-replica deployment that is a real
limit. On N replicas an attacker gets N times the allowance, because there is no
shared counter. That is stated here rather than discovered later: a limiter that
silently scales with your replica count is worse than none, because it is
believed.

It is also not a defence against a distributed source. It raises the cost of a
single-origin brute force and stops one client exhausting the service; it does
not stop a botnet.

WINDOW CHOICE. Sliding window over fixed buckets: a fixed 60s bucket lets an
attacker send 2x the limit across a boundary (all of it in the last second of one
window and the first of the next). The sliding form costs one deque per client
and removes that edge entirely.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Limit:
    requests: int
    window_seconds: int

    def __str__(self) -> str:
        return f"{self.requests}/{self.window_seconds}s"


# Tightest limits on the unauthenticated surface, which is the only part an
# attacker can reach without first obtaining a credential.
#
# `redeem-token` and `verify-otp` are credential-guessing surfaces: 10/min is
# generous for a human following a link and useless for a brute force.
# `request-otp` is lower still because each call sends a real message — an
# attacker who cannot guess the code can still use the endpoint to spam a
# patient's handset, which is its own harm.
ROUTE_LIMITS: dict[str, Limit] = {
    "/api/auth/redeem-token": Limit(10, 60),
    "/api/auth/verify-otp": Limit(10, 60),
    "/api/auth/request-otp": Limit(5, 300),
    "/api/messaging/delivery-webhook": Limit(120, 60),
    "/api/messaging/telegram-webhook": Limit(120, 60),
}

# Everything authenticated. Deliberately loose: a clinic generating summaries
# during a busy morning is normal traffic, and a limiter that fires on real
# clinical use gets switched off.
DEFAULT_LIMIT = Limit(120, 60)

# Never limited. An uptime probe that trips the limiter takes the service out of
# the load balancer for a reason it invented itself.
EXEMPT_PATHS = frozenset({"/health", "/ready", "/docs", "/openapi.json", "/redoc"})

_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

# Bound on distinct clients tracked. Without this the dict is an unbounded
# allocation driven by attacker-chosen IPs — the limiter becomes the memory-
# exhaustion vector it was added to prevent.
MAX_TRACKED_CLIENTS = 20_000


def _client_id(request: Request) -> str:
    """
    Identify the caller.

    X-Forwarded-For is taken because Railway terminates TLS upstream and the
    socket peer is always the proxy — without it every request shares one bucket
    and the first busy clinician locks out the clinic. The LEFTMOST entry is
    used, which is client-controlled and therefore spoofable; that is acceptable
    for rate limiting (an attacker rotating it is doing more work than just
    rotating IPs) and would NOT be acceptable for authorisation.
    """
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _limit_for(path: str) -> Limit:
    return ROUTE_LIMITS.get(path, DEFAULT_LIMIT)


def check(request: Request) -> tuple[bool, Limit, int]:
    """Returns (allowed, limit, retry_after_seconds)."""
    path = request.url.path
    limit = _limit_for(path)
    key = (_client_id(request), path)
    now = time.monotonic()

    bucket = _hits[key]
    cutoff = now - limit.window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.popleft()

    if len(bucket) >= limit.requests:
        retry = max(1, int(bucket[0] + limit.window_seconds - now) + 1)
        return False, limit, retry

    bucket.append(now)

    # Evict cold buckets once the map grows. Cheap and only on growth.
    if len(_hits) > MAX_TRACKED_CLIENTS:
        stale = [k for k, v in _hits.items() if not v or v[-1] < now - 900]
        for k in stale[: len(stale) // 2 or 1]:
            _hits.pop(k, None)

    return True, limit, 0


async def rate_limit_middleware(request: Request, call_next):
    if request.url.path in EXEMPT_PATHS or request.method == "OPTIONS":
        # OPTIONS is exempt because a CORS preflight is issued by the browser,
        # not the caller, and limiting it would break the page rather than the
        # attacker.
        return await call_next(request)

    allowed, limit, retry = check(request)
    if not allowed:
        # Logged at warning with the path but WITHOUT the client id: on this
        # service the id can be a patient's IP, which is personal data, and a
        # rate-limit log is not a place to start collecting it.
        logger.warning("Rate limit exceeded on %s (limit %s)", request.url.path, limit)
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests. Slow down and try again shortly."},
            headers={"Retry-After": str(retry)},
        )
    return await call_next(request)


def reset() -> None:
    """Clear all counters. For tests only."""
    _hits.clear()
