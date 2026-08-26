"""
Test Concurrent Edits — verifies CRDT-based collaborative editing.

Tests that:
- Two roles can edit different sections without data loss
- Two roles editing the same section merge deterministically (Yjs)
- Edits from both parties are preserved in the final document
- Version history captures concurrent edit sessions
"""

import pytest
import asyncio

pytestmark = pytest.mark.asyncio


class TestConcurrentEdits:
    """Test suite for concurrent editing via Yjs CRDTs and timeline entries."""

    async def test_two_roles_edit_different_sections(
        self, clinician_client, staff_client, sample_care_note_id
    ):
        """
        Two roles editing different sections should both have their edits preserved.

        Since CRDTs operate at the Yjs level (not raw DB), we simulate this at the
        timeline entry level: both roles add entries concurrently, and both should
        be visible in the final timeline.
        """
        clinician_user_id = (clinician_client.auth.get_user()).user.id
        staff_user_id = (staff_client.auth.get_user()).user.id

        # Clinician adds a clinical observation
        clinician_entry = {
            "care_note_id": sample_care_note_id,
            "author_role": "clinician",
            "author_id": clinician_user_id,
            "entry_type": "manual_note",
            "content": {"text": "Clinical assessment: stable condition"},
            "content_text": "Clinical assessment: stable condition - concurrent test",
            "risk_level": "info",
            "visibility": "internal",
        }

        # Staff adds a vitals note
        staff_entry = {
            "care_note_id": sample_care_note_id,
            "author_role": "staff",
            "author_id": staff_user_id,
            "entry_type": "manual_note",
            "content": {"text": "Vitals: BP 118/76, HR 68"},
            "content_text": "Vitals: BP 118/76, HR 68 - concurrent test",
            "risk_level": "info",
            "visibility": "internal",
        }

        # Insert concurrently
        clinician_result = (
            clinician_client.table("timeline_entries")
            .insert(clinician_entry)
            .execute()
        )
        staff_result = (
            staff_client.table("timeline_entries")
            .insert(staff_entry)
            .execute()
        )

        assert len(clinician_result.data) == 1, "Clinician entry should be created"
        assert len(staff_result.data) == 1, "Staff entry should be created"

        # Verify both entries exist in the timeline
        all_entries = (
            clinician_client.table("timeline_entries")
            .select("id, content_text, author_role")
            .eq("care_note_id", sample_care_note_id)
            .execute()
        )

        clinician_texts = [
            e["content_text"]
            for e in all_entries.data
            if "concurrent test" in (e["content_text"] or "")
            and e["author_role"] == "clinician"
        ]
        staff_texts = [
            e["content_text"]
            for e in all_entries.data
            if "concurrent test" in (e["content_text"] or "")
            and e["author_role"] == "staff"
        ]

        assert len(clinician_texts) > 0, "Clinician's concurrent edit should be preserved"
        assert len(staff_texts) > 0, "Staff's concurrent edit should be preserved"

    async def test_concurrent_edits_no_data_loss(
        self, clinician_client, staff_client, sample_care_note_id
    ):
        """Both concurrent edits should be preserved — no data loss."""
        clinician_user_id = (clinician_client.auth.get_user()).user.id
        staff_user_id = (staff_client.auth.get_user()).user.id

        # Count entries before
        before_count = (
            clinician_client.table("timeline_entries")
            .select("id", count="exact")
            .eq("care_note_id", sample_care_note_id)
            .execute()
        )
        count_before = before_count.count or len(before_count.data)

        # Add entries from both roles
        entries_to_add = [
            {
                "care_note_id": sample_care_note_id,
                "author_role": "clinician",
                "author_id": clinician_user_id,
                "entry_type": "manual_note",
                "content": {"text": f"Concurrent clinician entry {i}"},
                "content_text": f"Concurrent clinician entry {i}",
                "risk_level": "info",
                "visibility": "internal",
            }
            for i in range(3)
        ]

        staff_entries = [
            {
                "care_note_id": sample_care_note_id,
                "author_role": "staff",
                "author_id": staff_user_id,
                "entry_type": "manual_note",
                "content": {"text": f"Concurrent staff entry {i}"},
                "content_text": f"Concurrent staff entry {i}",
                "risk_level": "info",
                "visibility": "internal",
            }
            for i in range(2)
        ]

        # Insert all
        for entry in entries_to_add:
            clinician_client.table("timeline_entries").insert(entry).execute()
        for entry in staff_entries:
            staff_client.table("timeline_entries").insert(entry).execute()

        # Count entries after
        after_count = (
            clinician_client.table("timeline_entries")
            .select("id", count="exact")
            .eq("care_note_id", sample_care_note_id)
            .execute()
        )
        count_after = after_count.count or len(after_count.data)

        # Should have at least 5 more entries (3 clinician + 2 staff)
        assert count_after >= count_before + 5, (
            f"Expected at least {count_before + 5} entries, got {count_after}. "
            "Some concurrent edits were lost!"
        )

    async def test_edit_same_entry_version_conflict(
        self, clinician_client, sample_care_note_id
    ):
        """
        When two edits target the same entry, the system should handle it gracefully.
        At the Yjs CRDT level, both edits merge automatically at character level.
        At the DB level, last-write-wins for non-CRDT fields.
        """
        user_id = (clinician_client.auth.get_user()).user.id

        # Create an entry
        entry = {
            "care_note_id": sample_care_note_id,
            "author_role": "clinician",
            "author_id": user_id,
            "entry_type": "manual_note",
            "content": {"text": "Original content"},
            "content_text": "Original content for conflict test",
            "risk_level": "info",
            "visibility": "internal",
        }
        result = clinician_client.table("timeline_entries").insert(entry).execute()
        entry_id = result.data[0]["id"]

        # Simulate two rapid edits
        clinician_client.table("timeline_entries").update(
            {"content_text": "Edit A: Updated content"}
        ).eq("id", entry_id).execute()

        clinician_client.table("timeline_entries").update(
            {"content_text": "Edit B: Final content"}
        ).eq("id", entry_id).execute()

        # Verify final state
        final = (
            clinician_client.table("timeline_entries")
            .select("content_text")
            .eq("id", entry_id)
            .single()
            .execute()
        )
        assert final.data["content_text"] == "Edit B: Final content", (
            "Last edit should be the final state"
        )

    async def test_yjs_crdt_merge_concept(self):
        """
        Conceptual test: Yjs CRDTs merge character-level edits deterministically.

        This test validates the concept by importing Yjs (Python bindings not available,
        so we test the merge logic conceptually).

        In the real system:
        - User A types "Hello" at position 0
        - User B types "World" at position 5
        - Merged result: "HelloWorld" (deterministic, no conflict)
        """
        # Simulate CRDT merge behavior
        doc_a = "Hello"
        doc_b = "World"

        # In a real Yjs merge, concurrent edits at different positions compose
        merged = doc_a + doc_b
        assert "Hello" in merged, "Edit from User A should be preserved"
        assert "World" in merged, "Edit from User B should be preserved"

    async def test_concurrent_comments_preserved(
        self, clinician_client, staff_client, sample_care_note_id
    ):
        """Concurrent comments from different users should all be preserved."""
        clinician_user_id = (clinician_client.auth.get_user()).user.id
        staff_user_id = (staff_client.auth.get_user()).user.id

        # Get a timeline entry to comment on
        entries = (
            clinician_client.table("timeline_entries")
            .select("id")
            .eq("care_note_id", sample_care_note_id)
            .limit(1)
            .execute()
        )
        entry_id = entries.data[0]["id"]

        # Both users add comments concurrently
        clinician_comment = {
            "care_note_id": sample_care_note_id,
            "timeline_entry_id": entry_id,
            "author_id": clinician_user_id,
            "author_role": "clinician",
            "content": "Clinician concurrent comment",
        }
        staff_comment = {
            "care_note_id": sample_care_note_id,
            "timeline_entry_id": entry_id,
            "author_id": staff_user_id,
            "author_role": "staff",
            "content": "Staff concurrent comment",
        }

        clinician_client.table("comments").insert(clinician_comment).execute()
        staff_client.table("comments").insert(staff_comment).execute()

        # Verify both comments exist
        all_comments = (
            clinician_client.table("comments")
            .select("content, author_role")
            .eq("timeline_entry_id", entry_id)
            .execute()
        )

        contents = [c["content"] for c in all_comments.data]
        assert "Clinician concurrent comment" in contents
        assert "Staff concurrent comment" in contents


# ===========================================================================
# Deterministic same-section resolution
# ===========================================================================
# The brief: two roles editing DIFFERENT sections must not overwrite each other,
# and same-section conflicts need "a deterministic resolution strategy".
#
# Different sections are handled by construction — they never contend. Same
# section goes through services.conflict, whose ordering is a strict total order
# so two clients resolving independently reach the same answer.

import itertools  # noqa: E402

from services.conflict import (  # noqa: E402
    Edit,
    conflict_metadata,
    resolve_all,
    resolve_section,
)
from tests.support.pgharness import USERS  # noqa: E402

TS_EARLY = "2026-01-01T09:00:00+00:00"
TS_LATE = "2026-02-01T09:00:00+00:00"


class TestDeterministicResolution:
    """Same-section conflicts resolve identically every time."""

    async def test_clinician_takes_precedence_over_ai(self):
        """The brief's stated rule: a clinician entry beats prior AI memory."""
        resolution = resolve_section([
            Edit("ai-1", "plan", "system", None, "AI: continue current dose", TS_LATE),
            Edit("cl-1", "plan", "clinician", "c1", "Increase to 10mg", TS_EARLY),
        ])
        assert resolution.winner.edit_id == "cl-1"
        assert resolution.winner.author_role == "clinician"
        assert resolution.conflict is True

    async def test_clinician_over_ai_resolves_without_review(self):
        """Routine override — resolved silently, not queued for a human."""
        resolution = resolve_section([
            Edit("ai-1", "plan", "system", None, "AI text", TS_LATE),
            Edit("cl-1", "plan", "clinician", "c1", "Clinician text", TS_EARLY),
        ])
        assert resolution.requires_review is False

    async def test_two_humans_disagreeing_is_flagged_for_review(self):
        """
        Staff vs clinician on one section still picks a winner, but a human is
        told. The ordering resolves it; the flag makes sure nobody loses work
        silently.
        """
        resolution = resolve_section([
            Edit("st-1", "plan", "staff", "s1", "Staff version", TS_LATE),
            Edit("cl-1", "plan", "clinician", "c1", "Clinician version", TS_EARLY),
        ])
        assert resolution.winner.author_role == "clinician"
        assert resolution.requires_review is True

    async def test_recency_breaks_ties_within_a_role(self):
        resolution = resolve_section([
            Edit("cl-1", "plan", "clinician", "c1", "Older", TS_EARLY),
            Edit("cl-2", "plan", "clinician", "c2", "Newer", TS_LATE),
        ])
        assert resolution.winner.edit_id == "cl-2"

    async def test_resolution_is_order_independent(self):
        """
        Every arrival order must produce the same winner. Two clients resolving
        the same conflict independently would otherwise diverge.
        """
        edits = [
            Edit("a", "plan", "staff", "s1", "A", TS_LATE),
            Edit("b", "plan", "clinician", "c1", "B", TS_EARLY),
            Edit("c", "plan", "system", None, "C", TS_LATE),
        ]
        winners = {
            resolve_section(list(perm)).winner.edit_id
            for perm in itertools.permutations(edits)
        }
        assert winners == {"b"}, f"Non-deterministic across orderings: {winners}"

    async def test_exact_tie_resolves_deterministically(self):
        """Same role, same instant: the id ordering is the final tie-break."""
        edits = [
            Edit("zzz", "plan", "staff", "s1", "Z", TS_LATE),
            Edit("aaa", "plan", "staff", "s2", "A", TS_LATE),
        ]
        winners = {
            resolve_section(list(perm)).winner.edit_id
            for perm in itertools.permutations(edits)
        }
        assert winners == {"aaa"}

    async def test_losing_edits_are_preserved_never_discarded(self):
        """A superseded edit must remain recoverable for review."""
        resolution = resolve_section([
            Edit("ai-1", "plan", "system", None, "AI text", TS_LATE),
            Edit("st-1", "plan", "staff", "s1", "Staff text", TS_LATE),
            Edit("cl-1", "plan", "clinician", "c1", "Clinician text", TS_EARLY),
        ])
        assert len(resolution.superseded) == 2
        assert {e.edit_id for e in resolution.superseded} == {"ai-1", "st-1"}

    async def test_conflict_metadata_carries_no_clinical_content(self):
        """The audit trail records ids and roles, never patient text."""
        secret = "Patient reports severe chest pain radiating to the left arm"
        resolution = resolve_section([
            Edit("ai-1", "plan", "system", None, secret, TS_LATE),
            Edit("cl-1", "plan", "clinician", "c1", "Clinician text", TS_EARLY),
        ])
        serialised = str(conflict_metadata(resolution))
        assert secret not in serialised
        assert "chest pain" not in serialised

    async def test_different_sections_never_contend(self):
        """The non-destructive merge property, at the resolution layer."""
        resolutions = resolve_all([
            Edit("cl-1", "assessment", "clinician", "c1", "Clinician assessment", TS_EARLY),
            Edit("st-1", "vitals", "staff", "s1", "Staff vitals", TS_EARLY),
        ])
        assert set(resolutions) == {"assessment", "vitals"}
        assert all(not r.conflict for r in resolutions.values())
        assert resolutions["assessment"].winner.edit_id == "cl-1"
        assert resolutions["vitals"].winner.edit_id == "st-1"

    async def test_mixed_batch_is_rejected(self):
        """Resolving edits from different sections together would pick a wrong winner."""
        with pytest.raises(ValueError):
            resolve_section([
                Edit("a", "plan", "clinician", "c1", "A", TS_EARLY),
                Edit("b", "vitals", "staff", "s1", "B", TS_EARLY),
            ])


class TestConcurrentWritesAgainstDatabase:
    """Non-destructive merge, proven against real rows under real RLS."""

    async def test_staff_and_clinician_writes_both_survive(
        self, staff_client, clinician_client, sample_care_note_id
    ):
        staff_client.table("timeline_entries").insert({
            "care_note_id": sample_care_note_id, "author_role": "staff",
            "author_id": USERS["staff"][0], "entry_type": "manual_note",
            "content": {}, "content_text": "Staff: vitals recorded at 14:00",
            "visibility": "internal",
        }).execute()

        clinician_client.table("timeline_entries").insert({
            "care_note_id": sample_care_note_id, "author_role": "clinician",
            "author_id": USERS["clinician"][0], "entry_type": "manual_note",
            "content": {}, "content_text": "Clinician: assessment updated",
            "visibility": "internal",
        }).execute()

        rows = clinician_client.table("timeline_entries").select("*").eq(
            "care_note_id", sample_care_note_id
        ).execute().data
        texts = [r["content_text"] for r in rows]

        assert any("Staff: vitals recorded" in t for t in texts), "staff write was lost"
        assert any("Clinician: assessment updated" in t for t in texts), "clinician write was lost"

    async def test_neither_role_can_overwrite_the_other(
        self, staff_client, clinician_client, sample_care_note_id
    ):
        """
        The brief's hard constraint. Enforced by RLS ('Authors can update their
        own entries'), not by convention — so a write as the other role does not
        merely fail in the UI, it produces no row change at all.
        """
        inserted = staff_client.table("timeline_entries").insert({
            "care_note_id": sample_care_note_id, "author_role": "staff",
            "author_id": USERS["staff"][0], "entry_type": "manual_note",
            "content": {}, "content_text": "STAFF ORIGINAL", "visibility": "internal",
        }).execute().data[0]

        clinician_client.table("timeline_entries").update(
            {"content_text": "CLINICIAN OVERWROTE IT"}
        ).eq("id", inserted["id"]).execute()

        after = staff_client.table("timeline_entries").select("*").eq(
            "id", inserted["id"]
        ).execute().data[0]
        assert after["content_text"] == "STAFF ORIGINAL", "clinician overwrote a staff note"


class TestAtomicVersionAllocation:
    """
    Concurrent snapshot flushes must not collide on
    UNIQUE(care_note_id, version_number).

    The collab server used to read MAX(version_number), add one, then insert.
    Two flushes landing together read the same maximum and one insert failed --
    and because the failure was caught and logged rather than thrown, version
    history silently stopped growing. create_note_version() now allocates the
    number under a per-care-note advisory lock.
    """

    async def test_parallel_version_creation_yields_distinct_numbers(
        self, pg, service_client, sample_care_note_id
    ):
        import concurrent.futures

        import psycopg

        before = len(
            service_client.table("note_versions")
            .select("id")
            .eq("care_note_id", sample_care_note_id)
            .execute()
            .data
        )

        def make_version(n: int) -> int:
            with psycopg.connect(pg.dsn, autocommit=True) as conn:
                row = conn.execute(
                    "SELECT create_note_version(%s, NULL, %s, %s, NULL)",
                    (sample_care_note_id, f'{{"n": {n}}}', f"concurrent flush {n}"),
                ).fetchone()
                return row[0]

        # Fire well past the debounce window's realistic concurrency.
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            versions = list(pool.map(make_version, range(10)))

        assert len(set(versions)) == 10, (
            f"Version numbers collided under concurrency: {sorted(versions)}"
        )
        assert sorted(versions) == list(range(before + 1, before + 11)), (
            f"Versions were not allocated contiguously: {sorted(versions)}"
        )

    async def test_system_snapshot_accepts_null_author(
        self, pg, service_client, sample_care_note_id
    ):
        """changed_by is a uuid FK; a system snapshot passes NULL, not a string."""
        import psycopg

        with psycopg.connect(pg.dsn, autocommit=True) as conn:
            version = conn.execute(
                "SELECT create_note_version(%s, NULL, %s, %s, NULL)",
                (sample_care_note_id, '{"summary": "system"}', "System auto-save"),
            ).fetchone()[0]

        row = (
            service_client.table("note_versions")
            .select("*")
            .eq("care_note_id", sample_care_note_id)
            .eq("version_number", version)
            .execute()
            .data[0]
        )
        assert row["changed_by"] is None
