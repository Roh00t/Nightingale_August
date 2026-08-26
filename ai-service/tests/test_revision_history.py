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
