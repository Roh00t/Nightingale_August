"""
Audit 9 (delivery tracing), 12 (retraction) and 16 (highlight provenance).

All three were schema-without-behaviour in the first assessment pass. These
cover the behaviour that closed them, and in particular the properties that make
each one worth having rather than decorative.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

import main
import routers.messaging as mr
import services.messaging as msg
from services.auth import CallerIdentity, require_caller
from services.provenance import normalise_quote, quote_hash
from tests.support.pgharness import CLINIC_1


# ---------------------------------------------------------------------------
# Audit 16 — quote fingerprinting
# ---------------------------------------------------------------------------


class TestQuoteFingerprint:
    def test_cosmetic_edits_do_not_invalidate_a_highlight(self):
        """
        Reflowing a paragraph or fixing capitalisation has not changed what the
        note says. If those counted as modification, "Source Modified" would
        appear constantly and clinicians would learn to ignore it — which
        destroys the signal exactly when it matters.
        """
        assert quote_hash("Lisinopril 10mg daily") == quote_hash("  lisinopril   10mg   daily ")

    def test_a_changed_dose_does_invalidate(self):
        """The case the tag exists for."""
        assert quote_hash("Lisinopril 10mg daily") != quote_hash("Lisinopril 1.0mg daily")

    def test_digits_and_punctuation_are_preserved(self):
        """Normalisation must not fold anything that carries clinical meaning."""
        assert normalise_quote("K+ 6.4 mmol/L") == "k+ 6.4 mmol/l"
        assert quote_hash("eGFR 45") != quote_hash("eGFR 54")


# ---------------------------------------------------------------------------
# Audit 9 — delivery tracing
# ---------------------------------------------------------------------------


class TestDeliveryStatusSemantics:
    def test_sent_is_not_receipt(self):
        """
        The distinction the whole module exists for. `sent` means the provider
        accepted it from us — the same category of claim as "we generated a
        link" — and must never render as the patient having it.
        """
        assert not msg.DeliveryRecord("1", "sent", "sms", "+6591234567").confirmed_received
        assert not msg.DeliveryRecord("1", "queued", "sms", "+6591234567").confirmed_received
        assert msg.DeliveryRecord("1", "delivered", "sms", "+6591234567").confirmed_received
        assert msg.DeliveryRecord("1", "read", "sms", "+6591234567").confirmed_received

    def test_failure_outranks_success_in_the_status_order(self):
        """
        A `failed` callback arriving after `sent` is the truth; the earlier
        optimism is not. Ordering encodes that so a late failure is never
        discarded as out-of-order.
        """
        assert msg.STATUS_ORDER["failed"] > msg.STATUS_ORDER["delivered"]
        assert msg.STATUS_ORDER["undeliverable"] > msg.STATUS_ORDER["read"]

    def test_malformed_number_is_rejected_before_dispatch(self, monkeypatch):
        """
        A malformed number is the commonest reason a patient never receives
        anything, and the carrier's rejection arrives asynchronously if at all —
        so it is caught here rather than discovered by absence.
        """
        for bad in ["91234567", "+65 9123 4567", "", "not-a-number"]:
            with pytest.raises(msg.DeliveryError):
                msg.queue_delivery(
                    clinic_id=CLINIC_1, profile_id="p1",
                    channel="sms", destination=bad,
                )

    def test_no_provider_means_queued_not_sent(self, monkeypatch):
        """
        With no provider wired, nothing was contacted. The row must say so
        rather than defaulting to something reassuring — a comfortable lie here
        is the exact failure this module was written to prevent.
        """
        monkeypatch.delenv("MESSAGING_PROVIDER_API_KEY", raising=False)
        assert msg.provider_configured() is False

        captured = {}

        class _Tbl:
            def insert(self, row): captured.update(row); return self
            def update(self, patch): captured["_update"] = patch; return self
            def eq(self, *a): return self
            def execute(self): return type("R", (), {"data": [{"id": "d1", **captured}]})()

        monkeypatch.setattr(msg, "get_service_client", lambda: type("C", (), {"table": lambda s, n: _Tbl()})())
        rec = msg.queue_delivery(
            clinic_id=CLINIC_1, profile_id="p1",
            channel="whatsapp", destination="+6591234567",
        )
        assert rec.status == "queued"
        assert rec.confirmed_received is False
        assert "_update" not in captured, "nothing may advance the status without a provider"


class TestDeliveryWebhookAuthenticity:
    def test_unsigned_webhook_is_rejected(self, monkeypatch):
        """
        This endpoint cannot require a Supabase JWT — a provider has none — so
        it is the one open write path into delivery state. Unsigned, anyone who
        learns the URL can mark every message delivered, and a green tick that
        means nothing is worse than no tracking at all.
        """
        monkeypatch.setenv("MESSAGING_WEBHOOK_SECRET", "s3cret")
        r = TestClient(main.app).post(
            "/api/messaging/delivery-webhook",
            json={"provider_message_id": "m1", "status": "delivered"},
        )
        assert r.status_code == 401

    def test_bad_signature_is_rejected(self, monkeypatch):
        monkeypatch.setenv("MESSAGING_WEBHOOK_SECRET", "s3cret")
        r = TestClient(main.app).post(
            "/api/messaging/delivery-webhook",
            json={"provider_message_id": "m1", "status": "delivered"},
            headers={"X-Signature": "deadbeef"},
        )
        assert r.status_code == 401

    def test_missing_secret_fails_closed(self, monkeypatch):
        """
        A deployment that forgets the secret loses status updates rather than
        silently accepting forged ones.
        """
        monkeypatch.delenv("MESSAGING_WEBHOOK_SECRET", raising=False)
        body = {"provider_message_id": "m1", "status": "delivered"}
        raw = json.dumps(body).encode()
        r = TestClient(main.app).post(
            "/api/messaging/delivery-webhook", content=raw,
            headers={"Content-Type": "application/json", "X-Signature": "x"},
        )
        assert r.status_code == 503

    def test_valid_signature_is_accepted(self, monkeypatch):
        monkeypatch.setenv("MESSAGING_WEBHOOK_SECRET", "s3cret")
        monkeypatch.setattr(mr, "apply_provider_status", lambda **kw: {"status": kw["status"]})
        body = {"provider_message_id": "m1", "status": "delivered"}
        raw = json.dumps(body).encode()
        sig = hmac.new(b"s3cret", raw, hashlib.sha256).hexdigest()
        r = TestClient(main.app).post(
            "/api/messaging/delivery-webhook", content=raw,
            headers={"Content-Type": "application/json", "X-Signature": sig},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "delivered"

    def test_unknown_message_returns_200_not_404(self, monkeypatch):
        """
        Providers retry non-2xx indefinitely. An unknown id is permanent, so
        retrying it forever buys nothing and buries real callbacks in noise.
        """
        monkeypatch.setenv("MESSAGING_WEBHOOK_SECRET", "s3cret")
        monkeypatch.setattr(mr, "apply_provider_status", lambda **kw: None)
        body = {"provider_message_id": "nope", "status": "delivered"}
        raw = json.dumps(body).encode()
        sig = hmac.new(b"s3cret", raw, hashlib.sha256).hexdigest()
        r = TestClient(main.app).post(
            "/api/messaging/delivery-webhook", content=raw,
            headers={"Content-Type": "application/json", "X-Signature": sig},
        )
        assert r.status_code == 200
        assert r.json()["result"] == "unknown_message"


class TestOutOfOrderWebhooks:
    def test_late_sent_does_not_regress_a_delivered_message(self, monkeypatch):
        """
        Providers do not guarantee callback order. A duplicate `sent` arriving
        after `delivered` would make a completed delivery look stuck, and staff
        would chase a patient who already has the message.
        """
        state = {"id": "d1", "status": "delivered"}
        updates = []

        class _Tbl:
            def select(self, *a): return self
            def eq(self, *a): return self
            def limit(self, *a): return self
            def update(self, patch): updates.append(patch); return self
            def execute(self): return type("R", (), {"data": [state]})()

        monkeypatch.setattr(msg, "get_service_client",
                            lambda: type("C", (), {"table": lambda s, n: _Tbl()})())
        result = msg.apply_provider_status(provider_message_id="m1", status="sent")
        assert result["status"] == "delivered"
        assert updates == [], "an out-of-order webhook must not write"

    def test_failure_after_delivered_is_applied(self, monkeypatch):
        """The inverse: a genuine late failure must not be discarded."""
        state = {"id": "d1", "status": "delivered"}
        updates = []

        class _Tbl:
            def select(self, *a): return self
            def eq(self, *a): return self
            def limit(self, *a): return self
            def update(self, patch): updates.append(patch); return self
            def execute(self): return type("R", (), {"data": [state]})()

        monkeypatch.setattr(msg, "get_service_client",
                            lambda: type("C", (), {"table": lambda s, n: _Tbl()})())
        msg.apply_provider_status(
            provider_message_id="m1", status="failed", failure_reason="handset unreachable"
        )
        assert updates and updates[0]["status"] == "failed"
        assert updates[0]["failure_reason"] == "handset unreachable"


# ---------------------------------------------------------------------------
# Audit 12 — retraction
# ---------------------------------------------------------------------------


class TestRetraction:
    @pytest.fixture
    def client(self, monkeypatch):
        async def _caller() -> CallerIdentity:
            return CallerIdentity(
                user_id="c1", role="clinician", clinic_id=CLINIC_1, display_name="Dr. Tan",
            )
        import routers.patient_message as pm
        monkeypatch.setattr(pm, "resolve_care_note",
                            lambda cid, **kw: {"id": cid, "clinic_id": CLINIC_1, "patient_id": "p1"})
        main.app.dependency_overrides[require_caller] = _caller
        yield TestClient(main.app)
        main.app.dependency_overrides.clear()

    def test_reason_is_required_and_substantive(self, client):
        """
        The reason is shown to the patient verbatim. A one-word reason produces
        a notice that alarms without informing.
        """
        r = client.post("/api/ai/retract-patient-message", json={
            "care_note_id": "n1", "entry_id": "e1", "reason": "oops",
        }, headers={"Authorization": "Bearer stub"})
        assert r.status_code == 422

    def test_staff_cannot_retract(self, monkeypatch):
        """
        Withdrawal is a clinician speech act, matching who may approve a send.
        Staff cannot withdraw advice they were not permitted to issue.
        """
        async def _staff() -> CallerIdentity:
            return CallerIdentity(user_id="s1", role="staff",
                                  clinic_id=CLINIC_1, display_name="Nurse Lim")
        main.app.dependency_overrides[require_caller] = _staff
        try:
            r = TestClient(main.app).post("/api/ai/retract-patient-message", json={
                "care_note_id": "n1", "entry_id": "e1",
                "reason": "The dose in the previous message was incorrect.",
            }, headers={"Authorization": "Bearer stub"})
            assert r.status_code == 403
        finally:
            main.app.dependency_overrides.clear()

    def test_retraction_marks_and_notifies(self, client, monkeypatch):
        """
        Two writes, both required. Marking alone only helps someone who goes
        back and re-reads the old message — which is exactly what a patient who
        already acted on it will not do.
        """
        calls = {}
        import services.supabase_writer as w

        class _Tbl:
            def update(self, patch): calls["update"] = patch; return self
            def eq(self, *a): return self
            def execute(self): return type("R", (), {"data": [{"id": "e1"}]})()

        monkeypatch.setattr(w, "get_service_client",
                            lambda: type("C", (), {"table": lambda s, n: _Tbl()})())
        monkeypatch.setattr(w, "insert_patient_visible_entry",
                            lambda **kw: (calls.update(notice=kw), {"id": "e2"})[1])

        import routers.patient_message as pm
        monkeypatch.setattr(pm, "retract_patient_message", w.retract_patient_message)

        r = client.post("/api/ai/retract-patient-message", json={
            "care_note_id": "n1", "entry_id": "e1",
            "reason": "The dose in the previous message was incorrect.",
        }, headers={"Authorization": "Bearer stub"})
        assert r.status_code == 200, r.text

        assert calls["update"]["is_retracted"] is True
        # The original text is marked, never removed: the patient remembers being
        # told something and an auditor must be able to see what.
        assert "content_text" not in calls["update"]
        assert "withdrawn" in calls["notice"]["content_text"].lower()
        assert calls["notice"]["metadata"]["retracts_entry_id"] == "e1"
