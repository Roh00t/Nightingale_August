"""
Test Revision History — verifies version tracking and revert functionality.

Tests that:
- Editing a note increments the version number
- Reverting restores content to a prior state
- Audit log fields (changed_by, change_summary) are populated
- Version snapshots contain meaningful content
"""

import pytest

pytestmark = pytest.mark.asyncio


class TestRevisionHistory:
    """Test suite for revision history and versioning."""

    async def test_version_exists_for_care_note(
        self, clinician_client, sample_care_note_id
    ):
        """Care note should have at least one version in history."""
        result = (
            clinician_client.table("note_versions")
            .select("*")
            .eq("care_note_id", sample_care_note_id)
            .order("version_number", desc=True)
            .execute()
        )
        assert len(result.data) > 0, "Care note should have at least one version"

    async def test_version_number_increments(
        self, clinician_client, sample_care_note_id
    ):
        """Adding a new version should increment the version_number."""
        # Get current max version
        result = (
            clinician_client.table("note_versions")
            .select("version_number")
            .eq("care_note_id", sample_care_note_id)
            .order("version_number", desc=True)
            .limit(1)
            .execute()
        )
        current_max = result.data[0]["version_number"] if result.data else 0

        # Create a new version
        new_version = {
            "care_note_id": sample_care_note_id,
            "version_number": current_max + 1,
            "content_snapshot": {"summary": "Test version for history test"},
            "changed_by": (clinician_client.auth.get_user()).user.id,
            "change_summary": "Test: added version for history test",
        }
        insert_result = (
            clinician_client.table("note_versions")
            .insert(new_version)
            .execute()
        )
        assert len(insert_result.data) == 1, "Should create a new version"
        assert insert_result.data[0]["version_number"] == current_max + 1, (
            f"Version should be {current_max + 1}, got {insert_result.data[0]['version_number']}"
        )

    async def test_version_has_changed_by(
        self, clinician_client, sample_care_note_id
    ):
        """All versions should have the changed_by field populated."""
        result = (
            clinician_client.table("note_versions")
            .select("id, version_number, changed_by, change_summary")
            .eq("care_note_id", sample_care_note_id)
            .execute()
        )

        for version in result.data:
            assert version["changed_by"] is not None, (
                f"Version {version['version_number']} missing changed_by"
            )

    async def test_version_has_change_summary(
        self, clinician_client, sample_care_note_id
    ):
        """All versions should have a change_summary."""
        result = (
            clinician_client.table("note_versions")
            .select("id, version_number, change_summary")
            .eq("care_note_id", sample_care_note_id)
            .execute()
        )

        for version in result.data:
            assert version["change_summary"] is not None and version["change_summary"] != "", (
                f"Version {version['version_number']} missing change_summary"
            )

    async def test_version_has_content_snapshot(
        self, clinician_client, sample_care_note_id
    ):
        """All versions should have a content_snapshot."""
        result = (
            clinician_client.table("note_versions")
            .select("id, version_number, content_snapshot")
            .eq("care_note_id", sample_care_note_id)
            .execute()
        )

        for version in result.data:
            assert version["content_snapshot"] is not None, (
                f"Version {version['version_number']} missing content_snapshot"
            )

    async def test_revert_restores_prior_state(
        self, clinician_client, sample_care_note_id
    ):
        """Reverting to a prior version should restore that version's content."""
        # Get all versions
        result = (
            clinician_client.table("note_versions")
            .select("*")
            .eq("care_note_id", sample_care_note_id)
            .order("version_number", desc=False)
            .execute()
        )
        assert len(result.data) >= 2, "Need at least 2 versions for revert test"

        old_version = result.data[0]
        current_version = result.data[-1]

        # "Revert" by creating a new version with old content
        revert_version = {
            "care_note_id": sample_care_note_id,
            "version_number": current_version["version_number"] + 1,
            "content_snapshot": old_version["content_snapshot"],
            "changed_by": (clinician_client.auth.get_user()).user.id,
            "change_summary": f"Reverted to version {old_version['version_number']}",
        }
        insert_result = (
            clinician_client.table("note_versions")
            .insert(revert_version)
            .execute()
        )
        assert len(insert_result.data) == 1

        # Verify the reverted version has the old content
        reverted = insert_result.data[0]
        assert reverted["content_snapshot"] == old_version["content_snapshot"], (
            "Reverted version should match the content of the old version"
        )

    async def test_versions_ordered_chronologically(
        self, clinician_client, sample_care_note_id
    ):
        """Versions should be ordered by version_number."""
        result = (
            clinician_client.table("note_versions")
            .select("version_number, created_at")
            .eq("care_note_id", sample_care_note_id)
            .order("version_number", desc=False)
            .execute()
        )

        for i in range(1, len(result.data)):
            assert result.data[i]["version_number"] > result.data[i - 1]["version_number"], (
                "Versions should be in ascending order"
            )

    async def test_audit_trail_preserved(
        self, clinician_client, sample_care_note_id
    ):
        """Each version creates an audit trail with who made the change."""
        result = (
            clinician_client.table("note_versions")
            .select("version_number, changed_by, change_summary, created_at")
            .eq("care_note_id", sample_care_note_id)
            .order("version_number", desc=False)
            .execute()
        )

        for version in result.data:
            assert version["changed_by"] is not None, "Audit trail: changed_by required"
            assert version["change_summary"] is not None, "Audit trail: change_summary required"
            assert version["created_at"] is not None, "Audit trail: created_at required"


# ===========================================================================
# Audit trail: who changed what, metadata only
# ===========================================================================
# The brief requires the audit log to show "who changed what (metadata only)".
# The second half is the security-relevant half: an audit trail that quotes
# clinical text becomes a second, less-protected copy of the record. These tests
# assert attribution is present AND that patient content is not.

from tests.support.pgharness import USERS  # noqa: E402

# Clinical strings that appear in the seeded notes. None may leak into the log.
SEEDED_PHI = [
    "Alice Wong",
    "Lisinopril",
    "eGFR dropped to 45",
    "dyspnea on exertion",
    "Potassium 5.1",
]


class TestAuditTrailIsMetadataOnly:
    async def test_interaction_log_attributes_every_action(
        self, clinician_client, sample_highlights
    ):
        """Every logged action names an actor, their role, and the target."""
        highlight_id = sample_highlights[0]["id"]
        clinician_client.table("interaction_log").insert({
            "user_id": USERS["clinician"][0],
            "user_role": "clinician",
            "action_type": "pin",
            "target_type": "highlight",
            "target_id": highlight_id,
            "target_metadata": {"keywords": ["renal"], "topic": "renal_function"},
        }).execute()

        rows = clinician_client.table("interaction_log").select("*").execute().data
        assert rows, "no audit rows recorded"
        for row in rows:
            assert row["user_id"], "audit row without an actor"
            assert row["user_role"], "audit row without a role"
            assert row["action_type"], "audit row without an action"
            assert row["target_id"], "audit row without a target"

    async def test_interaction_log_contains_no_clinical_content(self, service_client):
        """
        Read the whole log with RLS bypassed — the strongest form of the check,
        since it sees every row rather than one role's slice.
        """
        rows = service_client.table("interaction_log").select("*").execute().data
        blob = str(rows)
        for secret in SEEDED_PHI:
            assert secret not in blob, (
                f"Audit log leaked clinical content: {secret!r} found in interaction_log"
            )

    async def test_audit_metadata_is_topics_not_transcripts(self, service_client):
        """target_metadata carries keywords and topics, never note text."""
        rows = service_client.table("interaction_log").select("target_metadata").execute().data
        for row in rows:
            meta = row.get("target_metadata") or {}
            assert set(meta).issubset({"keywords", "topic", "action", "count", "archived_at"}), (
                f"Unexpected audit metadata keys: {sorted(meta)}"
            )
            for value in meta.get("keywords", []):
                assert len(str(value)) < 40, f"Keyword looks like free text: {value!r}"

    async def test_versions_record_who_changed_what(self, clinician_client, sample_care_note_id):
        """note_versions attributes each snapshot and describes the change."""
        versions = (
            clinician_client.table("note_versions")
            .select("*")
            .eq("care_note_id", sample_care_note_id)
            .order("version_number", desc=False)
            .execute()
            .data
        )
        assert versions, "no versions seeded"
        for v in versions:
            assert v["changed_by"], f"version {v['version_number']} has no author"
            assert v["change_summary"], f"version {v['version_number']} has no summary"


class TestRevertRestoresState:
    async def test_revert_restores_prior_content_and_adds_a_version(
        self, clinician_client, sample_care_note_id
    ):
        """
        Revert is additive, not destructive: restoring v1 writes a NEW version
        carrying v1's content, so the history of what happened stays intact and
        the revert itself is auditable.
        """
        versions = (
            clinician_client.table("note_versions")
            .select("*")
            .eq("care_note_id", sample_care_note_id)
            .order("version_number", desc=False)
            .execute()
            .data
        )
        original = versions[0]
        latest = versions[-1]
        assert original["content_snapshot"] != latest["content_snapshot"], (
            "seed fixture needs versions with differing content for this test"
        )

        clinician_client.table("note_versions").insert({
            "care_note_id": sample_care_note_id,
            "version_number": latest["version_number"] + 1,
            "content_snapshot": original["content_snapshot"],
            "changed_by": USERS["clinician"][0],
            "change_summary": f"Reverted to version {original['version_number']}",
        }).execute()

        after = (
            clinician_client.table("note_versions")
            .select("*")
            .eq("care_note_id", sample_care_note_id)
            .order("version_number", desc=True)
            .execute()
            .data
        )
        assert len(after) == len(versions) + 1, "revert did not create a new version"
        assert after[0]["content_snapshot"] == original["content_snapshot"], (
            "reverted version does not carry the prior state"
        )
        # Nothing was destroyed.
        assert any(v["version_number"] == latest["version_number"] for v in after)

    async def test_version_numbers_are_unique_per_care_note(
        self, clinician_client, sample_care_note_id
    ):
        """
        UNIQUE(care_note_id, version_number) is what stops a concurrent flush
        silently clobbering a snapshot.
        """
        from postgrest.exceptions import APIError

        existing = (
            clinician_client.table("note_versions")
            .select("version_number")
            .eq("care_note_id", sample_care_note_id)
            .order("version_number", desc=True)
            .limit(1)
            .execute()
            .data
        )
        with pytest.raises(APIError):
            clinician_client.table("note_versions").insert({
                "care_note_id": sample_care_note_id,
                "version_number": existing[0]["version_number"],  # duplicate
                "content_snapshot": {"summary": "duplicate"},
                "changed_by": USERS["clinician"][0],
                "change_summary": "should be rejected",
            }).execute()
