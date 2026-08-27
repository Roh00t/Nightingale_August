"""
/api/ai/send-patient-message — the maker-checker gate on the patient path.

The gate module and its unit tests already existed. Nothing called them. A
clinician pressed Send and the browser inserted straight into `timeline_entries`,
so the check that exists to stop "10mg" becoming "100mg" was never in the path
that a message actually travels. These tests cover the endpoint that closed that,
and in particular the two properties that make the check meaningful rather than
decorative:

  * the grounding sources are read from the record by the SERVER, so a draft
    cannot supply its own grounding;
  * the write happens only on the passing branch of the same call, so there is no
    window between checking and writing and no way to skip the check by not
    calling it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
import routers.patient_message as pm
from services.auth import CallerIdentity, require_caller
from services.supabase_writer import AccessDenied
from tests.support.pgharness import CLINIC_1

CLINICIAN_ID = "00000000-0000-0000-0000-000000000001"
CARE_NOTE_ID = "11111111-1111-1111-1111-111111111111"

# What the record actually says. Every number the clinician sends must trace here.
RECORD = [
    "Prescribed Lisinopril 10mg daily. BP 128/78 at today's visit.",
    "eGFR 45. Review in 3 months.",
]


@pytest.fixture
def writes():
    """Captures inserts so a test can assert nothing was written."""
    return []


@pytest.fixture
def client(monkeypatch, writes):
    """Clinician caller; Supabase reads/writes stubbed at the writer boundary."""

    async def _caller() -> CallerIdentity:
        return CallerIdentity(
            user_id=CLINICIAN_ID, role="clinician",
            clinic_id=CLINIC_1, display_name="Dr. Tan",
        )

    def _resolve(care_note_id, *, caller_clinic_id):
        if care_note_id != CARE_NOTE_ID:
            raise AccessDenied(f"Care note {care_note_id} not found")
        return {"id": CARE_NOTE_ID, "clinic_id": caller_clinic_id, "patient_id": "p1"}

    def _insert(**kw):
        writes.append(kw)
        return {"id": "entry-1", **kw}

    monkeypatch.setattr(pm, "resolve_care_note", _resolve)
    monkeypatch.setattr(pm, "fetch_grounding_sources", lambda _id: RECORD)
    monkeypatch.setattr(pm, "insert_patient_visible_entry", _insert)

    main.app.dependency_overrides[require_caller] = _caller
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def send(client, draft, care_note_id=CARE_NOTE_ID, **extra):
    return client.post(
        "/api/ai/send-patient-message",
        json={"care_note_id": care_note_id, "draft": draft, **extra},
        headers={"Authorization": "Bearer stub"},
    )


class TestGateBlocksBeforeTheWrite:
    def test_fabricated_dose_is_blocked_and_nothing_is_written(self, client, writes):
        """The canonical catastrophe: a dose the record does not contain."""
        r = send(client, "Please take Lisinopril 100000000mg every morning.")
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["verdict"] == "blocked_ungrounded"
        assert "100000000mg" in detail["ungrounded_terms"]
        assert writes == [], "a blocked message must not reach the record"

    def test_unit_swap_is_blocked(self, client, writes):
        """10ml is not 10mg. The number matches; the dose does not."""
        r = send(client, "Take Lisinopril 10ml every morning.")
        assert r.status_code == 422
        assert "10ml" in r.json()["detail"]["ungrounded_terms"]
        assert writes == []

    def test_prohibited_speech_act_is_blocked(self, client, writes):
        """Diagnosis is a clinician's to deliver, not a drafted message's."""
        r = send(client, "You have chronic kidney disease and it is terminal.")
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["verdict"] == "blocked_prohibited"
        assert detail["prohibited_hits"]
        assert writes == []

    def test_block_names_the_offending_token(self, client):
        """
        The UI highlights what the clinician must fix, so the token has to come
        back. A bare "blocked" would leave them re-reading their own message.
        """
        detail = send(client, "Lisinopril 250mg daily.").json()["detail"]
        assert detail["ungrounded_terms"] == ["250mg"]
        assert detail["message"]

    def test_empty_draft_is_rejected(self, client, writes):
        assert send(client, "").status_code == 422
        assert writes == []


class TestGroundedMessagesPass:
    def test_grounded_draft_is_sent(self, client, writes):
        r = send(client, "Keep taking Lisinopril 10mg daily. Your BP was 128/78.")
        assert r.status_code == 200, r.text
        assert r.json()["verdict"] == "passed"
        assert len(writes) == 1

    def test_sent_entry_is_patient_visible_and_attributed(self, client, writes):
        send(client, "Keep taking Lisinopril 10mg daily.")
        row = writes[0]
        assert row["author_id"] == CLINICIAN_ID
        assert row["author_role"] == "clinician"
        # The patient is told a human approved it, and who.
        assert "Dr. Tan" in row["content_text"]
        assert row["metadata"]["patient_gate_verdict"] == "passed"
        assert row["metadata"]["human_approved"] is True

    def test_clinician_edits_are_what_get_checked(self, client, writes):
        """
        The AI's draft is irrelevant by this point. Whatever the clinician typed
        is what the patient reads, so it is what the gate screens — this is the
        edit-after-draft path the old flow skipped entirely.
        """
        assert send(client, "Lisinopril 10mg daily.").status_code == 200
        assert send(client, "Lisinopril 40mg daily.").status_code == 422
        assert len(writes) == 1


class TestGroundingCannotBeSuppliedByTheCaller:
    def test_request_supplied_sources_are_ignored(self, client, writes):
        """
        The security property the whole gate rests on.

        If the caller could pass the sources, a fabricated dose could be sent as
        its own grounding and would verify against itself. The request model has
        no sources field, and the server reads the record instead — so an attempt
        to smuggle one in changes nothing.
        """
        r = send(
            client,
            "Take Lisinopril 999mg daily.",
            sources=["Lisinopril 999mg daily."],
            entries=[{"content": "Lisinopril 999mg daily."}],
        )
        assert r.status_code == 422
        assert "999mg" in r.json()["detail"]["ungrounded_terms"]
        assert writes == []


class TestAuthorization:
    def test_unauthenticated_is_rejected(self):
        assert TestClient(main.app).post(
            "/api/ai/send-patient-message",
            json={"care_note_id": CARE_NOTE_ID, "draft": "hello"},
        ).status_code in (401, 403)

    def test_staff_cannot_approve_patient_facing_content(self, monkeypatch, writes):
        """
        Approving a clinical message to a patient is a clinician speech act.
        Matches APPROVER_ROLES in the gate, so the endpoint and the module agree.
        """
        async def _staff() -> CallerIdentity:
            return CallerIdentity(
                user_id="s1", role="staff", clinic_id=CLINIC_1, display_name="Nurse Lim",
            )
        monkeypatch.setattr(pm, "fetch_grounding_sources", lambda _id: RECORD)
        main.app.dependency_overrides[require_caller] = _staff
        try:
            r = TestClient(main.app).post(
                "/api/ai/send-patient-message",
                json={"care_note_id": CARE_NOTE_ID, "draft": "Lisinopril 10mg daily."},
                headers={"Authorization": "Bearer stub"},
            )
            assert r.status_code == 403
            assert writes == []
        finally:
            main.app.dependency_overrides.clear()

    def test_other_clinic_care_note_is_not_found(self, client, writes):
        """Same shape as a genuine miss: no probing which ids exist."""
        r = send(client, "Lisinopril 10mg daily.",
                 care_note_id="22222222-2222-2222-2222-222222222222")
        assert r.status_code == 404
        assert writes == []
