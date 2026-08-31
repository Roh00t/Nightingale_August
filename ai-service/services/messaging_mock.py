"""
Mock messaging provider — closes the delivery loop without touching a patient.

WHAT THIS IS FOR. `_dispatch()` raised NotImplementedError, so every delivery sat
at `queued` and the monotonic status machine, the signature check and the
out-of-order guard were all unexercised end to end. This provides a provider
that behaves like a real one — accepts a message, returns an id, calls back
asynchronously — so those paths run for real in development and in tests.

THE THREE RULES THIS FILE EXISTS TO ENFORCE.

1. It cannot run by accident. Enabling requires `MESSAGING_PROVIDER=mock`
   explicitly. A missing variable does not fall back to mock — it falls back to
   no provider at all, which is the honest `queued` state. Defaulting to mock
   when unconfigured is how a staging convenience reaches production: the
   deployment looks like it is sending, every message shows delivered, and
   nobody is contacted.

2. It cannot reach a real person. Destinations are restricted to a reserved test
   range, and anything else is refused. Without this, a mock wired to a real
   number is indistinguishable from a live send that silently goes nowhere —
   and the first time it matters is when a patient does not receive an
   appointment they were told about.

3. It cannot forge a receipt. The callback goes through the SAME signed webhook
   endpoint as a real provider, over HTTP, with a real HMAC. It has no privileged
   path into delivery state. If the mock could advance status by calling
   `apply_provider_status` directly, it would prove nothing about the mechanism
   that matters and would create a second, unauthenticated write path — exactly
   the forged green tick the design refuses.

Rule 3 is the important one. A mock that shortcuts the signature check tests the
happy path of a system whose actual risk is authenticity.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import uuid

logger = logging.getLogger(__name__)

PROVIDER_NAME = "mock"

# Reserved test destinations, mirroring the magic-number convention Twilio and
# others use. Each drives a specific terminal state so the failure paths are
# exercisable, not just the success one — a provider integration that has only
# ever been tested against success is a provider integration that has not been
# tested.
#
# +65 8000 0001  delivers
# +65 8000 0002  accepted, then fails (unreachable handset)
# +65 8000 0003  rejected at dispatch (invalid number)
# +65 8000 0004  accepted, then never calls back (the silent case)
MOCK_DESTINATIONS: dict[str, str] = {
    "+6580000001": "delivered",
    "+6580000002": "failed",
    "+6580000003": "reject",
    "+6580000004": "silent",
}


class MockNotPermitted(RuntimeError):
    """Raised when the mock is asked to do something only a real provider may."""


def is_enabled() -> bool:
    """
    Explicit opt-in only.

    Note this is `== "mock"`, not `!= "live"`. An unset or misspelled value gives
    no provider rather than the mock, so a typo degrades to visibly-not-sending
    instead of invisibly-pretending-to-send.
    """
    return os.environ.get("MESSAGING_PROVIDER", "").strip().lower() == "mock"


def _webhook_url() -> str:
    base = os.environ.get("AI_SERVICE_SELF_URL", "http://localhost:8000").rstrip("/")
    return f"{base}/api/messaging/delivery-webhook"


def _fire_webhook(provider_message_id: str, status: str, reason: str | None = None) -> None:
    """
    Call our own webhook the way a provider would: over HTTP, signed, unprivileged.

    Deliberately not an in-process function call. The whole risk in this
    subsystem is authenticity of the callback, so the mock must traverse the
    signature check like anyone else. If it bypassed that, the tests would pass
    while the mechanism they are meant to prove went unexercised — and the mock
    would itself become an unauthenticated way to mark messages delivered.
    """
    secret = os.environ.get("MESSAGING_WEBHOOK_SECRET")
    if not secret:
        # Fail closed and loudly. Silently skipping the callback would leave the
        # delivery at `sent` forever, which reads as "stuck in transit" rather
        # than "misconfigured", and staff would chase a phantom.
        logger.error(
            "Mock provider cannot fire the webhook: MESSAGING_WEBHOOK_SECRET is "
            "unset. Delivery %s will remain unresolved.", provider_message_id,
        )
        return

    payload: dict[str, object] = {
        "provider_message_id": provider_message_id,
        "status": status,
    }
    if reason:
        payload["failure_reason"] = reason

    raw = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()

    try:
        import httpx

        resp = httpx.post(
            _webhook_url(),
            content=raw,
            headers={"Content-Type": "application/json", "X-Signature": signature},
            timeout=10.0,
        )
        logger.info(
            "Mock webhook %s -> %s (http %s)", provider_message_id, status, resp.status_code
        )
    except Exception:
        logger.exception("Mock webhook delivery failed for %s", provider_message_id)


def dispatch(channel: str, destination: str, body: str) -> tuple[str, str]:
    """
    Accept a message the way a provider does, and schedule its callback.

    Returns (provider_name, message_id) and leaves the caller to record `sent`.
    It does NOT return a delivered status: provider acceptance is not receipt,
    and a mock that conflated them would model the one thing this subsystem
    exists to keep apart.
    """
    if not is_enabled():
        raise MockNotPermitted("Mock provider called while MESSAGING_PROVIDER != 'mock'")

    outcome = MOCK_DESTINATIONS.get(destination)
    if outcome is None:
        # The rule that keeps a mock from contacting a real person. Refusing is
        # the only safe answer: silently dropping it would look like a send, and
        # actually sending is what the mock exists to avoid.
        raise MockNotPermitted(
            f"{destination} is not a reserved mock destination. The mock provider "
            f"refuses unknown numbers so it can never reach a real patient. "
            f"Use one of: {', '.join(sorted(MOCK_DESTINATIONS))}"
        )

    if outcome == "reject":
        raise RuntimeError("Invalid destination number (mock rejection at dispatch)")

    message_id = f"mock_{uuid.uuid4().hex[:16]}"
    logger.info("Mock provider accepted %s for %s -> %s", message_id, destination, outcome)

    if outcome == "silent":
        # Accepted and never confirmed. This is the most realistic failure and
        # the one a system is most likely to get wrong, because it is
        # indistinguishable from success right up until the patient does not
        # arrive. It must remain reachable in testing.
        return PROVIDER_NAME, message_id

    # Asynchronous, like a real callback. A synchronous webhook would hide
    # ordering bugs: the caller has not yet written `sent` when dispatch
    # returns, so an immediate `delivered` could be applied first and then
    # overwritten — which the monotonic guard is there to catch, and which only
    # shows up when the callback genuinely races the write.
    delay = float(os.environ.get("MOCK_WEBHOOK_DELAY_SECONDS", "0.5"))
    reason = "Handset unreachable (mock)" if outcome == "failed" else None
    timer = threading.Timer(delay, _fire_webhook, args=(message_id, outcome, reason))
    timer.daemon = True
    timer.start()

    return PROVIDER_NAME, message_id
