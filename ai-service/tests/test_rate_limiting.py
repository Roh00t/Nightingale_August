"""
Rate limiting on the unauthenticated surface.

Assessment §2.4: no limiter existed, so the five endpoints reachable without a
credential accepted unbounded volume — brute-force room on `/redeem-token` and
`/verify-otp`, and on `/request-otp` the ability to spam a patient's handset with
real messages.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from services import rate_limit


@pytest.fixture(autouse=True)
def _clean():
    rate_limit.reset()
    yield
    rate_limit.reset()


def _client() -> TestClient:
    return TestClient(main.app)


class TestUnauthenticatedSurfaceIsBounded:
    def test_redeem_token_is_throttled(self):
        """The credential-guessing surface. 10/min is generous for a human."""
        c = _client()
        codes = [
            c.post("/api/auth/redeem-token", json={"token": "x" * 40},
                   headers={"X-Forwarded-For": "203.0.113.7"}).status_code
            for _ in range(14)
        ]
        assert 429 in codes, "redeem-token accepted unbounded attempts"
        assert codes.index(429) >= rate_limit.ROUTE_LIMITS["/api/auth/redeem-token"].requests

    def test_request_otp_is_throttled_hardest(self):
        """
        Each call sends a real message. An attacker who cannot guess the code can
        still use this endpoint to spam a patient's handset — its own harm, and
        the reason this limit is the tightest.
        """
        limit = rate_limit.ROUTE_LIMITS["/api/auth/request-otp"]
        assert limit.requests <= 5
        c = _client()
        codes = [
            c.post("/api/auth/request-otp", json={"phone": "+6591234567"},
                   headers={"X-Forwarded-For": "203.0.113.8"}).status_code
            for _ in range(limit.requests + 3)
        ]
        assert 429 in codes

    def test_429_carries_retry_after(self):
        """A client that cannot tell how long to wait retries immediately."""
        c = _client()
        last = None
        for _ in range(20):
            last = c.post("/api/auth/verify-otp", json={"phone": "+6591234567", "code": "000000"},
                          headers={"X-Forwarded-For": "203.0.113.9"})
            if last.status_code == 429:
                break
        assert last.status_code == 429
        assert int(last.headers["Retry-After"]) >= 1


class TestLimiterDoesNotBreakLegitimateUse:
    def test_clients_are_bucketed_separately(self):
        """
        One busy clinic must not lock out another. Without X-Forwarded-For every
        request shares the proxy's IP and the first heavy user throttles everyone.
        """
        c = _client()
        for _ in range(12):
            c.post("/api/auth/redeem-token", json={"token": "x" * 40},
                   headers={"X-Forwarded-For": "198.51.100.1"})
        other = c.post("/api/auth/redeem-token", json={"token": "x" * 40},
                       headers={"X-Forwarded-For": "198.51.100.2"})
        assert other.status_code != 429

    def test_health_and_ready_are_never_limited(self):
        """
        An uptime probe that trips the limiter takes the service out of the load
        balancer for a reason it invented itself.
        """
        c = _client()
        for _ in range(200):
            assert c.get("/health").status_code == 200
        assert c.get("/ready").status_code == 200

    def test_preflight_is_exempt(self):
        """
        A CORS preflight is issued by the browser, not the caller. Limiting it
        breaks the page rather than the attacker.
        """
        c = _client()
        for _ in range(150):
            r = c.options("/api/ai/summarize", headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            })
            assert r.status_code != 429

    def test_authenticated_limit_is_loose_enough_for_a_clinic(self):
        """
        A limiter that fires during real clinical use gets switched off. 120/min
        is well above a clinician generating summaries.
        """
        assert rate_limit.DEFAULT_LIMIT.requests >= 100


class TestLimiterCannotBecomeTheVulnerability:
    def test_client_map_is_bounded(self):
        """
        Without a cap the tracking dict is an unbounded allocation driven by
        attacker-chosen IPs — the limiter becomes the memory-exhaustion vector it
        was added to prevent.
        """
        assert rate_limit.MAX_TRACKED_CLIENTS <= 50_000
        c = _client()
        for i in range(300):
            c.post("/api/auth/redeem-token", json={"token": "x" * 40},
                   headers={"X-Forwarded-For": f"10.0.{i // 256}.{i % 256}"})
        assert len(rate_limit._hits) <= rate_limit.MAX_TRACKED_CLIENTS

    def test_sliding_window_not_fixed_buckets(self):
        """
        A fixed 60s bucket permits 2x the limit across a boundary. The sliding
        form removes that edge; assert the implementation keeps timestamps rather
        than a counter.
        """
        import inspect

        src = inspect.getsource(rate_limit.check)
        assert "popleft" in src, "not a sliding window — fixed buckets allow a 2x burst"
