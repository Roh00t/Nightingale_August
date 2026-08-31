"""
Delivery tracing for anything sent to a patient outside the app.

THE ASSUMPTION THIS EXISTS TO BREAK.

Generating a link and the patient receiving it are two different events, and
every system that conflates them tells staff a comfortable lie. The receptionist
sees "sent", believes the patient has their appointment details, and the patient
got nothing — wrong number, handset off, provider silently dropped it, number
recycled to someone else. Nobody finds out until the patient does not turn up.

So the status of a message starts at `queued` and **only a provider webhook can
advance it**. Our side of the handoff is evidence that we tried, and nothing
more. There is deliberately no code path that sets `delivered` from within this
service, and no user role has INSERT or UPDATE on `message_deliveries` — a
status a clinician can type is not evidence of anything.

MOCK-FIRST, like transcription. No SMS/WhatsApp provider is configured in this
build, and inventing one would produce exactly the false confidence described
above. With no provider, `send()` records a `queued` row and returns it
un-advanced, which renders in the UI as "not confirmed delivered" — the honest
state. Wiring a real provider means implementing `_dispatch` and pointing the
webhook at `POST /api/messaging/delivery-webhook`; nothing else changes.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from services.supabase_writer import get_service_client

logger = logging.getLogger(__name__)

# Statuses a provider may assert, in the order they may progress. Ordering is
# enforced so a late-arriving 'sent' webhook cannot overwrite a 'delivered' one
# — providers do not guarantee callback order, and regressing the status would
# make a delivered message look stuck.
STATUS_ORDER: dict[str, int] = {
    "queued": 0,
    "sent": 1,
    "delivered": 2,
    "read": 3,
    # Terminal failures sit above the success path so they always win: a
    # 'failed' callback after 'sent' is the truth, and the earlier optimism is not.
    "failed": 4,
    "undeliverable": 5,
}

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


class DeliveryError(RuntimeError):
    """Raised when a message cannot even be queued."""


@dataclass
class DeliveryRecord:
    id: str
    status: str
    channel: str
    destination: str

    @property
    def confirmed_received(self) -> bool:
        """
        Only `delivered` and `read` mean the patient's device got it.

        `sent` deliberately does not count. It means the provider accepted the
        message from us, which is the same category of claim as "we generated a
        link" — our side, not theirs.
        """
        return self.status in ("delivered", "read")


def provider_configured() -> bool:
    return bool(os.environ.get("MESSAGING_PROVIDER_API_KEY"))


def _dispatch(channel: str, destination: str, body: str) -> tuple[str, str]:
    """
    Hand the message to the provider. Returns (provider_name, message_id).

    Unimplemented on purpose. When a provider is wired here, the only correct
    behaviour is to return its message id and leave the status at `queued` —
    the acceptance response is not delivery, and treating it as such would
    recreate the failure this module exists to prevent.
    """
    raise NotImplementedError(
        "No messaging provider is wired. Implement _dispatch and set "
        "MESSAGING_PROVIDER_API_KEY to enable real sending."
    )


def queue_delivery(
    *,
    clinic_id: str,
    profile_id: str,
    channel: str,
    destination: str,
    care_note_id: str | None = None,
    entry_id: str | None = None,
    token_id: str | None = None,
    body: str | None = None,
) -> DeliveryRecord:
    """
    Record an intent to deliver, then attempt it.

    The row is written **before** dispatch, not after. If the process dies
    mid-send the record survives as `queued`, which is recoverable and visibly
    unresolved. Writing it afterwards would lose exactly the messages whose fate
    is least certain.
    """
    if channel in ("whatsapp", "sms") and not _E164.match(destination or ""):
        # Caught here rather than at the provider, because a malformed number is
        # the single most common reason a patient never receives anything, and
        # the provider's rejection arrives asynchronously if at all.
        raise DeliveryError(
            f"{destination!r} is not a valid E.164 number. A message to a "
            "malformed number fails silently at the carrier."
        )

    client = get_service_client()
    row = {
        "clinic_id": clinic_id,
        "profile_id": profile_id,
        "care_note_id": care_note_id,
        "entry_id": entry_id,
        "token_id": token_id,
        "channel": channel,
        "destination": destination,
        "status": "queued",
    }
    resp = client.table("message_deliveries").insert(row).execute()
    if not resp.data:
        raise DeliveryError("Could not record the delivery attempt")
    created = resp.data[0]

    if not provider_configured():
        logger.warning(
            "No messaging provider configured; delivery %s stays queued. The "
            "patient has NOT been contacted.",
            created["id"],
        )
        return DeliveryRecord(
            id=created["id"], status="queued", channel=channel, destination=destination
        )

    try:
        provider, message_id = _dispatch(channel, destination, body or "")
    except Exception as exc:
        client.table("message_deliveries").update({
            "status": "failed",
            "failure_reason": str(exc)[:500],
            "failed_at": "now()",
            "attempts": (created.get("attempts") or 0) + 1,
        }).eq("id", created["id"]).execute()
        logger.error("Delivery %s failed at dispatch: %s", created["id"], exc)
        return DeliveryRecord(
            id=created["id"], status="failed", channel=channel, destination=destination
        )

    # Accepted by the provider. Still not delivered — only a webhook says that.
    client.table("message_deliveries").update({
        "status": "sent",
        "provider": provider,
        "provider_message_id": message_id,
        "sent_at": "now()",
        "attempts": (created.get("attempts") or 0) + 1,
    }).eq("id", created["id"]).execute()

    return DeliveryRecord(
        id=created["id"], status="sent", channel=channel, destination=destination
    )


def apply_provider_status(
    *,
    provider_message_id: str,
    status: str,
    failure_reason: str | None = None,
) -> dict[str, Any] | None:
    """
    Advance a delivery from a provider webhook.

    Monotonic: a status that ranks lower than the one already recorded is
    ignored. Providers retry callbacks and do not guarantee ordering, so a
    duplicate `sent` arriving after `delivered` would otherwise make a completed
    delivery look stuck — and staff would chase a patient who already has the
    message.
    """
    if status not in STATUS_ORDER:
        raise DeliveryError(f"Unknown delivery status {status!r}")

    client = get_service_client()
    rows = (
        client.table("message_deliveries")
        .select("id, status")
        .eq("provider_message_id", provider_message_id)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        logger.warning("Webhook for unknown provider_message_id %s", provider_message_id)
        return None

    current = rows[0]
    if STATUS_ORDER[status] <= STATUS_ORDER.get(current["status"], 0):
        logger.info(
            "Ignoring out-of-order webhook: %s -> %s", current["status"], status
        )
        return current

    patch: dict[str, Any] = {"status": status, "updated_at": "now()"}
    if status == "delivered":
        patch["delivered_at"] = "now()"
    elif status in ("failed", "undeliverable"):
        patch["failed_at"] = "now()"
        patch["failure_reason"] = (failure_reason or "")[:500]

    updated = (
        client.table("message_deliveries").update(patch).eq("id", current["id"]).execute()
    )
    return (updated.data or [None])[0]


def unresolved_for_clinic(clinic_id: str) -> list[dict[str, Any]]:
    """
    Deliveries that have not reached the patient.

    This is the query the front desk actually needs — "what did we think we sent
    that never arrived" — and it treats `queued` and `sent` as unresolved
    alongside `failed`, because a message stuck in either is equally not in the
    patient's hands.
    """
    client = get_service_client()
    return (
        client.table("message_deliveries")
        .select("*")
        .eq("clinic_id", clinic_id)
        .in_("status", ["queued", "sent", "failed", "undeliverable"])
        .order("queued_at", desc=True)
        .limit(100)
        .execute()
    ).data or []
