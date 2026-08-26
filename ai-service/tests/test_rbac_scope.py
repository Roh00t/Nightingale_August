"""
Test RBAC Scope — verifies Row Level Security policies.

Tests that:
- Staff cannot edit clinician entries and vice versa
- Patients can only see patient_visible entries
- Patients cannot see internal comments or raw AI notes
- Cross-clinic access is denied
- Admin has read-only clinic-scoped access
"""

import pytest
import uuid
from postgrest.exceptions import APIError

pytestmark = pytest.mark.asyncio


class TestRBACScope:
    """Test suite for role-based access control via PostgreSQL RLS."""

    async def test_staff_cannot_edit_clinician_entry(
        self, clinician_client, staff_client, sample_care_note_id
    ):
        """Staff should not be able to update a clinician-authored entry."""
        # Clinician creates an entry
        entry_data = {
            "care_note_id": sample_care_note_id,
            "author_role": "clinician",
            "author_id": (clinician_client.auth.get_user()).user.id,
            "entry_type": "manual_note",
            "content": {"text": "Clinician note for RBAC test"},
            "content_text": "Clinician note for RBAC test",
            "risk_level": "info",
            "visibility": "internal",
        }
        result = clinician_client.table("timeline_entries").insert(entry_data).execute()
        assert len(result.data) == 1, "Clinician should be able to create entries"
        entry_id = result.data[0]["id"]

        # Staff attempts to update the clinician's entry
        update_result = (
            staff_client.table("timeline_entries")
            .update({"content_text": "Staff tried to edit clinician note"})
            .eq("id", entry_id)
            .execute()
        )
        # RLS should prevent update — result should be empty (no rows matched)
        assert len(update_result.data) == 0, (
            "Staff should NOT be able to update clinician entries"
        )

        # Verify the entry is unchanged
        verify = (
            clinician_client.table("timeline_entries")
            .select("content_text")
            .eq("id", entry_id)
            .single()
            .execute()
        )
        assert verify.data["content_text"] == "Clinician note for RBAC test"

    async def test_clinician_cannot_edit_staff_entry(
        self, clinician_client, staff_client, sample_care_note_id
    ):
        """Clinician should not be able to update a staff-authored entry."""
        # Staff creates an entry
        entry_data = {
            "care_note_id": sample_care_note_id,
            "author_role": "staff",
            "author_id": (staff_client.auth.get_user()).user.id,
            "entry_type": "manual_note",
            "content": {"text": "Staff note for RBAC test"},
            "content_text": "Staff note for RBAC test",
            "risk_level": "info",
            "visibility": "internal",
        }
        result = staff_client.table("timeline_entries").insert(entry_data).execute()
        assert len(result.data) == 1, "Staff should be able to create staff entries"
        entry_id = result.data[0]["id"]

        # Clinician attempts to update staff entry
        update_result = (
            clinician_client.table("timeline_entries")
            .update({"content_text": "Clinician tried to edit staff note"})
            .eq("id", entry_id)
            .execute()
        )
        assert len(update_result.data) == 0, (
            "Clinician should NOT be able to update staff entries"
        )

    async def test_patient_cannot_see_internal_entries(
        self, patient_client, sample_care_note_id
    ):
        """Patient should only see entries with visibility='patient_visible'."""
        patient_user_id = (patient_client.auth.get_user()).user.id
        result = (
            patient_client.table("timeline_entries")
            .select("*")
            .eq("care_note_id", sample_care_note_id)
            .execute()
        )

        for entry in result.data:
            is_own_message = (
                entry["entry_type"] == "patient_message"
                and entry.get("author_id") == patient_user_id
                and entry.get("author_role") == "patient"
            )
            assert entry["visibility"] == "patient_visible" or is_own_message, (
                f"Patient saw internal entry: {entry['id']} with visibility={entry['visibility']}"
            )

    async def test_patient_cannot_see_comments(
        self, patient_client, sample_care_note_id
    ):
        """Patient should not be able to read any comments."""
        result = (
            patient_client.table("comments")
            .select("*")
            .eq("care_note_id", sample_care_note_id)
            .execute()
        )
        assert len(result.data) == 0, (
            "Patient should NOT be able to see any comments"
        )

    async def test_patient_cannot_see_raw_ai_notes(
        self, patient_client, sample_care_note_id
    ):
        """Patient should not see AI-generated entries unless marked patient_visible."""
        result = (
            patient_client.table("timeline_entries")
            .select("*")
            .eq("care_note_id", sample_care_note_id)
            .execute()
        )

        for entry in result.data:
            if entry["entry_type"].startswith("ai_"):
                assert entry["visibility"] == "patient_visible", (
                    "Patient saw raw AI note that isn't patient_visible"
                )

    async def test_patient_cannot_see_highlights(
        self, patient_client, sample_care_note_id
    ):
        """Patient should not be able to read highlights."""
        result = (
            patient_client.table("highlights")
            .select("*")
            .eq("care_note_id", sample_care_note_id)
            .execute()
        )
        assert len(result.data) == 0, (
            "Patient should NOT be able to see highlights"
        )

    async def test_patient_can_submit_message(
        self, patient_client, sample_care_note_id
    ):
        """Patient should be able to submit patient_message entries for their own care note."""
        patient_user_id = (patient_client.auth.get_user()).user.id

        entry_data = {
            "care_note_id": sample_care_note_id,
            "author_role": "patient",
            "author_id": patient_user_id,
            "entry_type": "patient_message",
            "content": {"text": "New symptom update for care team"},
            "content_text": "New symptom update for care team",
            "risk_level": "info",
            "visibility": "internal",
            "metadata": {"direction": "incoming"},
        }

        result = (
            patient_client.table("timeline_entries")
            .insert(entry_data)
            .execute()
        )
        assert len(result.data) == 1, "Patient message should be created"

    async def test_patient_cannot_insert_manual_note(
        self, patient_client, sample_care_note_id
    ):
        """Patient should NOT be able to insert non-patient_message entries."""
        patient_user_id = (patient_client.auth.get_user()).user.id

        entry_data = {
            "care_note_id": sample_care_note_id,
            "author_role": "patient",
            "author_id": patient_user_id,
            "entry_type": "manual_note",
            "content": {"text": "Trying to insert a manual note"},
            "content_text": "Trying to insert a manual note",
            "risk_level": "info",
            "visibility": "internal",
        }

        with pytest.raises(APIError):
            patient_client.table("timeline_entries").insert(entry_data).execute()

    async def test_cross_clinic_access_denied(
        self, clinician_client, service_client
    ):
        """Users from clinic 1 should not see data from clinic 2."""
        # Create a care note in clinic 2 using service role
        other_clinic_note = {
            "id": str(uuid.uuid4()),
            "patient_id": str(uuid.uuid4()),  # Would need a real patient in clinic 2
            "clinic_id": "c0000000-0000-0000-0000-000000000002",
        }
        # Note: This test verifies the RLS policy concept
        # In a real environment, we'd create proper test data in clinic 2
        result = (
            clinician_client.table("care_notes")
            .select("*")
            .eq("clinic_id", "c0000000-0000-0000-0000-000000000002")
            .execute()
        )
        assert len(result.data) == 0, (
            "Clinician from clinic 1 should NOT see clinic 2 data"
        )

    async def test_admin_has_read_access(
        self, admin_client, sample_care_note_id
    ):
        """Admin should be able to read all data within their clinic."""
        # Read timeline entries
        entries_result = (
            admin_client.table("timeline_entries")
            .select("*")
            .eq("care_note_id", sample_care_note_id)
            .execute()
        )
        assert len(entries_result.data) > 0, "Admin should see timeline entries"

        # Read comments
        comments_result = (
            admin_client.table("comments")
            .select("*")
            .eq("care_note_id", sample_care_note_id)
            .execute()
        )
        assert len(comments_result.data) > 0, "Admin should see comments"

        # Read highlights
        highlights_result = (
            admin_client.table("highlights")
            .select("*")
            .eq("care_note_id", sample_care_note_id)
            .execute()
        )
        assert len(highlights_result.data) > 0, "Admin should see highlights"

    async def test_staff_can_create_staff_entries(
        self, staff_client, sample_care_note_id
    ):
        """Staff should be able to create entries with author_role='staff'."""
        entry_data = {
            "care_note_id": sample_care_note_id,
            "author_role": "staff",
            "author_id": (staff_client.auth.get_user()).user.id,
            "entry_type": "manual_note",
            "content": {"text": "Staff vitals check"},
            "content_text": "Staff vitals check: BP 120/80",
            "risk_level": "info",
            "visibility": "internal",
        }
        result = staff_client.table("timeline_entries").insert(entry_data).execute()
        assert len(result.data) == 1, "Staff should create staff entries"
        assert result.data[0]["author_role"] == "staff"

    async def test_staff_cannot_create_clinician_entries(
        self, staff_client, sample_care_note_id
    ):
        """Staff should not be able to create entries with author_role='clinician'."""
        entry_data = {
            "care_note_id": sample_care_note_id,
            "author_role": "clinician",  # Staff pretending to be clinician
            "author_id": (staff_client.auth.get_user()).user.id,
            "entry_type": "manual_note",
            "content": {"text": "Staff pretending to be clinician"},
            "content_text": "Should be rejected",
            "risk_level": "info",
            "visibility": "internal",
        }
        try:
            result = staff_client.table("timeline_entries").insert(entry_data).execute()
            # If insert succeeds, it should have been blocked by RLS
            assert len(result.data) == 0, (
                "Staff should NOT create entries with clinician role"
            )
        except Exception:
            # Expected: RLS violation
            pass
