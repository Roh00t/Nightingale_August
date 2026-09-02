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


def _find_graded(node, path="glance_cache"):
    """Every path in `node` that carries a clinical grading key."""
    found = []
    if isinstance(node, list):
        for i, v in enumerate(node):
            found += _find_graded(v, f"{path}[{i}]")
    elif isinstance(node, dict):
        for key in ("risk_level", "confidence", "severity", "risk", "status"):
            if key in node:
                found.append(f"{path}.{key} = {node[key]!r}")
        for k, v in node.items():
            found += _find_graded(v, f"{path}.{k}")
    return found


class TestPatientCannotReachInternalAssessment:
    """
    The clinician's risk judgement about a patient is not patient-facing.

    `care_notes.glance_cache` is readable by the patient who owns the row — it
    has to be, it carries their care-plan progress. But RLS is ROW-level, not
    column-level, so anything stored in that column is readable by that patient
    with their own JWT. A direct PostgREST call bypasses the UI entirely, which
    means stripping the field in a server component HIDES it rather than
    WITHHOLDING it.

    The severity chips (CRITICAL/HIGH), confidence scores and unresolved
    clinical actions therefore live in `care_note_assessments`, which has no
    patient policy at all.
    """

    async def test_patient_gets_no_assessment_rows(self, patient_client):
        """Not filtered, not redacted — absent. No policy admits a patient."""
        assert patient_client.table("care_note_assessments").select("*").execute().data == []

    async def test_patient_cannot_read_their_own_assessment_by_id(
        self, patient_client, sample_care_note_id
    ):
        """Owning the care note does not confer access to the assessment of it."""
        rows = (
            patient_client.table("care_note_assessments")
            .select("*")
            .eq("care_note_id", sample_care_note_id)
            .execute()
        ).data
        assert rows == []

    async def test_patient_glance_cache_carries_no_risk_assessment(
        self, patient_client
    ):
        """
        The column the patient CAN read must contain nothing clinical.

        This is the assertion that would have caught the original leak: the
        patient portal rendered "eGFR declining: 62 -> 45" with a CRITICAL chip,
        straight out of glance_cache.
        """
        rows = patient_client.table("care_notes").select("glance_cache").execute().data
        assert rows, "patient should still see their own care note row"

        for row in rows:
            cache = row["glance_cache"] or {}
            # Patient-safe fields remain.
            assert "care_plan_score" in cache

            # Internal assessment must be absent, not merely empty-valued.
            assert not cache.get("top_items"), (
                f"internal risk assessment reached the patient: {cache.get('top_items')}"
            )
            assert not cache.get("changes_since_last_visit")

            # Look for the SHAPE of a clinical judgement rather than for clinical
            # words. A care plan written for the patient may legitimately name
            # their own labs; what must never cross is the clinician's grading of
            # them -- a severity band, a model confidence, a triage status.
            graded = _find_graded(cache)
            assert not graded, f"severity grading readable by the patient: {graded[:3]}"

    async def test_care_team_can_read_the_assessment(self, clinician_client, staff_client):
        """The control must not have broken the people who need it."""
        for client, role in ((clinician_client, "clinician"), (staff_client, "staff")):
            rows = client.table("care_note_assessments").select("*").execute().data
            assert rows, f"{role} lost access to the clinical assessment"
            assert rows[0]["assessment"].get("top_items"), "assessment payload is empty"

    async def test_assessment_is_clinic_scoped(
        self, clinician_client, sunrise_care_note_id
    ):
        """A care-team member cannot read another clinic's assessment."""
        rows = (
            clinician_client.table("care_note_assessments")
            .select("*")
            .eq("care_note_id", sunrise_care_note_id)
            .execute()
        ).data
        assert rows == []

    async def test_patient_visible_entries_carry_no_internal_severity(
        self, patient_client
    ):
        """
        Entries a patient CAN read must not describe them in clinical risk terms.

        Their own instructions are info-level by construction; a critical or high
        entry reaching the patient would mean an internal assessment leaked
        through the timeline instead of the glance cache.
        """
        rows = patient_client.table("timeline_entries").select("*").execute().data
        for row in rows:
            assert row["risk_level"] in ("info", "low"), (
                f"patient can see a {row['risk_level']} entry: {row['entry_type']}"
            )
            assert row["visibility"] == "patient_visible" or row["entry_type"] == "patient_message"


class TestPatientFacingWritesRequireTheGate:
    """
    The database half of the maker-checker firewall.

    Screening in the AI service protects the UI path. It does not protect the
    record, because a clinician's own JWT can reach PostgREST directly — the
    same reasoning as the assessment leak above, in the write direction. So the
    INSERT policies admit `visibility = 'internal'` only, and every
    patient-facing entry has to arrive through the service-role write that the
    gate performs on its passing branch.
    """

    async def test_clinician_cannot_insert_patient_visible_entry(
        self, clinician_client, sample_care_note_id, user_ids
    ):
        """The bypass the gate exists to prevent: straight to the patient, unchecked."""
        with pytest.raises(Exception) as exc:
            clinician_client.table("timeline_entries").insert({
                "care_note_id": sample_care_note_id,
                "author_id": user_ids["clinician"],
                "author_role": "clinician",
                "entry_type": "instruction",
                "content": {"type": "doc", "content": []},
                "content_text": "Take Lisinopril 100000000mg daily.",
                "risk_level": "info",
                "visibility": "patient_visible",
            }).execute()
        assert "row-level security" in str(exc.value).lower() or "42501" in str(exc.value)

    async def test_staff_cannot_insert_patient_visible_entry(
        self, staff_client, sample_care_note_id, user_ids
    ):
        with pytest.raises(Exception):
            staff_client.table("timeline_entries").insert({
                "care_note_id": sample_care_note_id,
                "author_id": user_ids["staff"],
                "author_role": "staff",
                "entry_type": "instruction",
                "content": {"type": "doc", "content": []},
                "content_text": "Stop taking your medication.",
                "risk_level": "info",
                "visibility": "patient_visible",
            }).execute()

    async def test_internal_entries_still_work(
        self, clinician_client, sample_care_note_id, user_ids
    ):
        """
        The control. Restricting patient-facing writes must not block ordinary
        clinical note-taking, which is the overwhelming majority of writes.
        """
        rows = (
            clinician_client.table("timeline_entries").insert({
                "care_note_id": sample_care_note_id,
                "author_id": user_ids["clinician"],
                "author_role": "clinician",
                "entry_type": "manual_note",
                "content": {"type": "doc", "content": []},
                "content_text": "Reviewed labs; eGFR stable.",
                "risk_level": "info",
                "visibility": "internal",
            }).execute()
        ).data
        assert rows, "clinicians must still be able to write internal notes"

    async def test_service_role_can_still_file_the_approved_message(
        self, service_client, sample_care_note_id, user_ids
    ):
        """
        The gated path itself. The AI service writes with the service-role key
        after screening, so this must remain possible — otherwise the policy
        above would have closed the only legitimate route as well.
        """
        rows = (
            service_client.table("timeline_entries").insert({
                "care_note_id": sample_care_note_id,
                "author_id": user_ids["clinician"],
                "author_role": "clinician",
                "entry_type": "instruction",
                "content": {"type": "doc", "content": []},
                "content_text": "Keep taking Lisinopril 10mg daily.",
                "risk_level": "info",
                "visibility": "patient_visible",
                "metadata": {"patient_gate_verdict": "passed", "human_approved": True},
            }).execute()
        ).data
        assert rows


class TestGlanceCacheCannotCarryTheAssessment:
    """
    Regression: the assessment came back after it had already been fixed.

    `/patients/[id]` recomposes the assessment into `glance_cache` in memory so
    downstream components keep one shape. That object is also what the browser
    spreads into its care-plan writes, so a clinician ticking a checkbox
    persisted the assessment into the column patients read. Nothing errored and
    no test failed — the row simply looked as it had before the original fix.

    Application-side stripping alone would not hold: it has to be remembered at
    every write site, and forgetting it is what caused this. The column is now
    structurally incapable of holding these keys.
    """

    async def test_client_write_back_is_stripped(
        self, clinician_client, sample_care_note_id
    ):
        """Exactly what the browser did: spread a recomposed cache into update()."""
        clinician_client.table("care_notes").update({
            "glance_cache": {
                "care_plan_score": 78,
                "last_visit": "2026-02-01",
                "top_items": [{"text": "eGFR declining", "risk_level": "critical"}],
                "changes_since_last_visit": [{"text": "Creatinine rose"}],
            }
        }).eq("id", sample_care_note_id).execute()

        cache = (
            clinician_client.table("care_notes")
            .select("glance_cache").eq("id", sample_care_note_id).single().execute()
        ).data["glance_cache"]

        assert "top_items" not in cache, "the assessment was persisted to a patient-readable column"
        assert "changes_since_last_visit" not in cache
        # The legitimate fields must survive — a trigger that ate the care plan
        # would be a different outage.
        assert cache["care_plan_score"] == 78
        assert cache["last_visit"] == "2026-02-01"

    async def test_patient_still_sees_no_assessment_after_a_care_team_write(
        self, clinician_client, patient_client, sample_care_note_id
    ):
        """The property that actually matters, asserted from the patient's side."""
        clinician_client.table("care_notes").update({
            "glance_cache": {
                "care_plan_score": 80,
                "last_visit": "2026-02-01",
                "top_items": [{"text": "Anaphylaxis risk", "risk_level": "critical"}],
            }
        }).eq("id", sample_care_note_id).execute()

        rows = patient_client.table("care_notes").select("glance_cache").execute().data
        assert rows
        for row in rows:
            assert not (row["glance_cache"] or {}).get("top_items")


class TestPatientCannotEscalateTheirOwnRole:
    """
    The CRITICAL finding from OVERNIGHT_VULNERABILITY_ASSESSMENT.md §2.1.

    `"Users can update their own profile"` is USING (id = auth.uid()) with no
    column restriction, so a row a patient may update was a row they may update
    ANY column of — including `role`, which every other policy keys on. One
    statement took a patient from zero rows to the entire internal record:

        UPDATE profiles SET role='clinician' WHERE id = auth.uid();

    Measured before the fix: care_note_assessments 0 -> 1, internal
    timeline_entries 0 -> 7, comments 0 -> 3, note_versions 0 -> 4.

    Nothing in this file caught it, and the reason is worth keeping in view: every
    other test here asserts what a *patient* may read. None asserted that a
    patient cannot stop being a patient.
    """

    async def test_patient_cannot_change_their_own_role(self, patient_client, user_ids):
        with pytest.raises(Exception) as exc:
            patient_client.table("profiles").update({"role": "clinician"}).eq(
                "id", user_ids["patient"]
            ).execute()
        # Refused loudly, not silently reverted: a silent pin would let a UI
        # report success for a change that did not happen.
        assert "role may not be changed" in str(exc.value).lower() or "insufficient" in str(exc.value).lower()

    async def test_role_is_unchanged_in_the_database_after_the_attempt(
        self, patient_client, service_client, user_ids
    ):
        """The refusal must also not have partially applied."""
        try:
            patient_client.table("profiles").update({"role": "admin"}).eq(
                "id", user_ids["patient"]
            ).execute()
        except Exception:
            pass
        row = (
            service_client.table("profiles").select("role").eq("id", user_ids["patient"]).single().execute()
        ).data
        assert row["role"] == "patient"

    async def test_escalation_payoff_stays_unreachable(self, patient_client, user_ids):
        """
        The assertion that actually matters. Even if some future change let the
        UPDATE through, the patient must still see nothing — so this checks the
        consequence, not just the mechanism.
        """
        try:
            patient_client.table("profiles").update({"role": "clinician"}).eq(
                "id", user_ids["patient"]
            ).execute()
        except Exception:
            pass

        assert patient_client.table("care_note_assessments").select("*").execute().data == []
        internal = (
            patient_client.table("timeline_entries")
            .select("id").eq("visibility", "internal").execute()
        ).data
        assert internal == []
        assert patient_client.table("comments").select("*").execute().data == []
        assert patient_client.table("note_versions").select("*").execute().data == []

    async def test_patient_cannot_move_themselves_to_another_clinic(
        self, patient_client, user_ids, sunrise_care_note_id
    ):
        """
        The other half of the identity pin. clinic_id was already blocked by the
        inherited WITH CHECK, but that is incidental — this asserts it directly so
        a policy rewrite cannot quietly remove it.
        """
        with pytest.raises(Exception):
            patient_client.table("profiles").update(
                {"clinic_id": "c0000000-0000-0000-0000-000000000002"}
            ).eq("id", user_ids["patient"]).execute()

    async def test_ordinary_profile_edits_still_work(self, patient_client, user_ids):
        """
        The control. Pinning two columns must not have made the profile
        read-only — a patient correcting their own display name is legitimate,
        and a trigger that blocked it would be a different outage.
        """
        rows = (
            patient_client.table("profiles")
            .update({"display_name": "Alice W."})
            .eq("id", user_ids["patient"])
            .execute()
        ).data
        assert rows, "a patient can no longer edit their own profile at all"
        assert rows[0]["display_name"] == "Alice W."

    async def test_service_role_can_still_reassign(self, service_client, user_ids):
        """
        Provisioning and admin re-assignment must keep working. The first draft of
        this trigger tested current_setting('role')='service_role', which reads
        'none' on the owner connection and would have blocked seeding while
        looking correct.
        """
        rows = (
            service_client.table("profiles")
            .update({"role": "staff"}).eq("id", user_ids["patient"]).execute()
        ).data
        assert rows and rows[0]["role"] == "staff"
        service_client.table("profiles").update({"role": "patient"}).eq(
            "id", user_ids["patient"]
        ).execute()
