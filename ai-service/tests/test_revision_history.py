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
        """
        Reverting must restore the ACTUAL prior note content.

        The previous version of this test was tautological: it inserted
        `content_snapshot = old["content_snapshot"]` and then asserted the
        inserted row equalled it. That proves the database stored what it was
        handed — it proves nothing about revert, and it passed while snapshots
        held descriptions like "Added follow-up notes" that could never be
        restored to.

        This version compares three distinct states and asserts they relate
        correctly.
        """
        versions = (
            clinician_client.table("note_versions")
            .select("*")
            .eq("care_note_id", sample_care_note_id)
            .order("version_number", desc=False)
            .execute()
        ).data
        assert len(versions) >= 2, "need at least two versions to revert between"

        target = versions[0]      # the state we want back
        current = versions[-1]    # where the note is now

        # Precondition: the two states must actually differ, or the assertion
        # below would hold trivially.
        assert target["content_snapshot"] != current["content_snapshot"], (
            "fixture is degenerate: first and last versions have identical content"
        )

        # Snapshots must carry real note content, not a description of a change.
        # A snapshot of "Added follow-up notes and medication" cannot be
        # reverted to — restoring it would replace the note with that sentence.
        target_text = (target["content_snapshot"] or {}).get("text", "")
        assert target_text, "content_snapshot has no 'text' — nothing to restore"
        assert len(target_text) > 40, (
            f"snapshot looks like a change description, not note content: {target_text!r}"
        )
        assert not target_text.lower().startswith(("added ", "updated ", "created ")), (
            f"snapshot is a changelog entry, not restorable content: {target_text!r}"
        )

        # Perform the revert.
        reverted = (
            clinician_client.table("note_versions")
            .insert({
                "care_note_id": sample_care_note_id,
                "version_number": current["version_number"] + 1,
                "content_snapshot": target["content_snapshot"],
                "changed_by": (clinician_client.auth.get_user()).user.id,
                "change_summary": f"Reverted to version {target['version_number']}",
            })
            .execute()
        ).data[0]

        # The note now reads as it did at the target version...
        assert reverted["content_snapshot"]["text"] == target_text
        # ...and no longer as it did immediately before the revert.
        assert reverted["content_snapshot"] != current["content_snapshot"], (
            "revert did not change the note state"
        )
        # Revert is additive: the superseded state is still recoverable.
        after = (
            clinician_client.table("note_versions")
            .select("version_number")
            .eq("care_note_id", sample_care_note_id)
            .execute()
        ).data
        assert len(after) == len(versions) + 1
        assert current["version_number"] in [v["version_number"] for v in after], (
            "the reverted-away state was destroyed"
        )

    async def test_every_snapshot_is_restorable_content(
        self, clinician_client, sample_care_note_id
    ):
        """
        Every snapshot must be something you could put back into the editor.

        This is what makes "revert to any previous version" meaningful rather
        than a button that writes a sentence into the note body.
        """
        versions = (
            clinician_client.table("note_versions")
            .select("version_number, content_snapshot")
            .eq("care_note_id", sample_care_note_id)
            .execute()
        ).data

        for v in versions:
            snapshot = v["content_snapshot"] or {}
            assert "text" in snapshot, (
                f"v{v['version_number']} snapshot has no restorable 'text' key"
            )
            assert snapshot["text"].strip(), f"v{v['version_number']} snapshot text is empty"

    async def test_successive_versions_differ(
        self, clinician_client, sample_care_note_id
    ):
        """A version that changed nothing is not a version."""
        versions = (
            clinician_client.table("note_versions")
            .select("version_number, content_snapshot")
            .eq("care_note_id", sample_care_note_id)
            .order("version_number", desc=False)
            .execute()
        ).data

        seen: list[str] = []
        for v in versions:
            text = (v["content_snapshot"] or {}).get("text", "")
            assert text not in seen, (
                f"v{v['version_number']} duplicates an earlier snapshot verbatim"
            )
            seen.append(text)

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
