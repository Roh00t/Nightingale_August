"""
Test Self-Learning Importance — verifies the learning feedback loop (Bonus).

Tests that:
- Clinician interactions (pin, accept, reject) are logged to interaction_log
- When a clinician pins a highlight, similar future highlights get boosted scores
- The importance scoring function considers learned weights
- Manual highlights by clinicians influence future AI scoring
- Rejecting a highlight type reduces score for similar content
"""

import pytest
import uuid

pytestmark = pytest.mark.asyncio


class TestSelfLearningImportance:
    """Test suite for the self-learning importance scoring system."""

    async def test_accept_logged_to_interaction_log(
        self, clinician_client, sample_highlights
    ):
        """Accepting a highlight should create an interaction_log entry."""
        if not sample_highlights:
            pytest.skip("No highlights available")

        highlight = sample_highlights[0]
        user_id = (clinician_client.auth.get_user()).user.id

        # Accept the highlight
        clinician_client.table("highlights").update(
            {"is_accepted": True}
        ).eq("id", highlight["id"]).execute()

        # Log the interaction
        log_entry = {
            "user_id": user_id,
            "user_role": "clinician",
            "action_type": "accept",
            "target_type": "highlight",
            "target_id": highlight["id"],
            "target_metadata": {
                "keywords": ["test", "acceptance"],
                "topic": "clinical_review",
                "risk_level": highlight["risk_level"],
            },
        }
        result = clinician_client.table("interaction_log").insert(log_entry).execute()
        assert len(result.data) == 1, "Interaction log entry should be created"

        # Verify the log entry
        verify = (
            clinician_client.table("interaction_log")
            .select("*")
            .eq("target_id", highlight["id"])
            .eq("action_type", "accept")
            .execute()
        )
        assert len(verify.data) >= 1, "Accept action should be in interaction log"

    async def test_pin_logged_to_interaction_log(
        self, clinician_client, sample_care_note_id, sample_highlights
    ):
        """Pinning a highlight should create an interaction_log entry."""
        if not sample_highlights:
            pytest.skip("No highlights available")

        highlight = sample_highlights[0]
        user_id = (clinician_client.auth.get_user()).user.id

        # Pin the highlight
        clinician_client.table("highlights").update(
            {"is_pinned": True}
        ).eq("id", highlight["id"]).execute()

        # Log the pin interaction
        log_entry = {
            "user_id": user_id,
            "user_role": "clinician",
            "action_type": "pin",
            "target_type": "highlight",
            "target_id": highlight["id"],
            "target_metadata": {
                "keywords": highlight.get("content_snippet", "").split()[:5],
                "topic": "pinned_by_clinician",
                "risk_level": highlight["risk_level"],
            },
        }
        result = clinician_client.table("interaction_log").insert(log_entry).execute()
        assert len(result.data) == 1, "Pin interaction should be logged"

    async def test_similar_content_gets_boosted_score(
        self, clinician_client, sample_care_note_id, sample_highlights
    ):
        """
        When a clinician pins a highlight from an AI-scribed note,
        new highlights for similar content should have increased importance_score.

        Simulates the learning loop:
        1. Clinician pins highlight about 'eGFR decline'
        2. System generates new highlight about similar kidney topic
        3. New highlight should have higher importance_score
        """
        if not sample_highlights:
            pytest.skip("No highlights available")

        user_id = (clinician_client.auth.get_user()).user.id

        # Step 1: Find and pin a highlight about kidney/eGFR
        kidney_highlight = None
        for h in sample_highlights:
            if "egfr" in h["content_snippet"].lower() or "kidney" in h["content_snippet"].lower():
                kidney_highlight = h
                break

        if not kidney_highlight:
            pytest.skip("No kidney-related highlight found")

        # Pin it
        clinician_client.table("highlights").update(
            {"is_pinned": True}
        ).eq("id", kidney_highlight["id"]).execute()

        # Log the interaction with keywords
        clinician_client.table("interaction_log").insert({
            "user_id": user_id,
            "user_role": "clinician",
            "action_type": "pin",
            "target_type": "highlight",
            "target_id": kidney_highlight["id"],
            "target_metadata": {
                "keywords": ["eGFR", "kidney", "decline", "CKD"],
                "topic": "renal_function",
            },
        }).execute()

        # Step 2: Query interaction log for similar content
        log_result = (
            clinician_client.table("interaction_log")
            .select("*")
            .eq("action_type", "pin")
            .execute()
        )

        pin_count = len([
            entry for entry in log_result.data
            if entry.get("target_metadata", {}).get("topic") == "renal_function"
        ])

        # Step 3: Verify that similar content has been interacted with
        assert pin_count >= 1, (
            "Interaction log should contain pin actions for renal_function topic"
        )

        # The importance scoring service would use this data to boost
        # similar future highlights. We verify the data is available.
        baseline_score = kidney_highlight["importance_score"]
        assert baseline_score > 0.5, (
            "Pinned highlight should have above-average importance score"
        )

    async def test_reject_reduces_similar_content_score(
        self, clinician_client, sample_care_note_id, sample_highlights
    ):
        """
        Rejecting a highlight should be logged, and the system should
        use this to reduce scores for similar future content.
        """
        if not sample_highlights:
            pytest.skip("No highlights available")

        user_id = (clinician_client.auth.get_user()).user.id

        # Find a low-importance highlight to reject
        low_highlight = min(sample_highlights, key=lambda h: h["importance_score"])

        # Reject it
        clinician_client.table("highlights").update(
            {"is_accepted": False}
        ).eq("id", low_highlight["id"]).execute()

        # Log the rejection
        clinician_client.table("interaction_log").insert({
            "user_id": user_id,
            "user_role": "clinician",
            "action_type": "reject",
            "target_type": "highlight",
            "target_id": low_highlight["id"],
            "target_metadata": {
                "keywords": low_highlight["content_snippet"].split()[:3],
                "topic": "rejected_content",
                "risk_level": low_highlight["risk_level"],
            },
        }).execute()

        # Verify rejection is logged
        log_check = (
            clinician_client.table("interaction_log")
            .select("*")
            .eq("target_id", low_highlight["id"])
            .eq("action_type", "reject")
            .execute()
        )
        assert len(log_check.data) >= 1, "Rejection should be logged"

    async def test_manual_highlight_increases_topic_weight(
        self, clinician_client, sample_care_note_id
    ):
        """
        When a clinician manually creates a highlight, the system should
        log this as a strong signal for importance scoring.
        """
        user_id = (clinician_client.auth.get_user()).user.id

        # Get an entry to highlight
        entries = (
            clinician_client.table("timeline_entries")
            .select("id")
            .eq("care_note_id", sample_care_note_id)
            .limit(1)
            .execute()
        )
        entry_id = entries.data[0]["id"]

        # Create a manual highlight
        manual_highlight = {
            "care_note_id": sample_care_note_id,
            "source_entry_id": entry_id,
            "content_snippet": "Patient education on medication adherence",
            "risk_reason": "Clinician flagged: important for patient compliance",
            "risk_level": "medium",
            "importance_score": 0.85,
            "provenance_pointer": {
                "source_type": "timeline_entry",
                "source_id": entry_id,
            },
            "created_by": user_id,
        }
        result = (
            clinician_client.table("highlights")
            .insert(manual_highlight)
            .execute()
        )
        assert len(result.data) == 1, "Manual highlight should be created"
        highlight_id = result.data[0]["id"]

        # Log the manual highlight action
        clinician_client.table("interaction_log").insert({
            "user_id": user_id,
            "user_role": "clinician",
            "action_type": "manual_highlight",
            "target_type": "highlight",
            "target_id": highlight_id,
            "target_metadata": {
                "keywords": ["medication", "adherence", "education"],
                "topic": "patient_compliance",
            },
        }).execute()

        # Verify the interaction was logged
        log_check = (
            clinician_client.table("interaction_log")
            .select("*")
            .eq("action_type", "manual_highlight")
            .eq("target_id", highlight_id)
            .execute()
        )
        assert len(log_check.data) == 1, (
            "Manual highlight creation should be logged for learning"
        )

    async def test_interaction_log_has_metadata_for_learning(
        self, clinician_client
    ):
        """
        All interaction log entries should have target_metadata with
        keywords/topics for the learning system to use.
        """
        result = (
            clinician_client.table("interaction_log")
            .select("id, action_type, target_metadata")
            .execute()
        )

        for entry in result.data:
            metadata = entry.get("target_metadata", {})
            # At minimum, entries used for learning should have some metadata
            assert metadata is not None, (
                f"Interaction {entry['id']} has null target_metadata"
            )


# ===========================================================================
# The learning loop, exercised end to end
# ===========================================================================
# The suite above asserts that interactions are LOGGED. That is necessary but
# not sufficient: the brief requires proving that subsequent suggestions show
# increased priority as a result. These tests drive the real scorer
# (services.importance.compute_importance_score) against real interaction_log
# rows and assert the score actually moves.

import asyncio  # noqa: E402

from services.importance import (  # noqa: E402
    batch_score,
    compute_importance_score,
    set_interaction_source,
)
from tests.support.pgharness import CLINIC_1, CLINIC_2, USERS  # noqa: E402


@pytest.fixture
def wire_scorer_to_db(service_client):
    """
    Point the importance scorer at the test database.

    Production reads interaction_log from Supabase; here it reads the same table
    from the ephemeral cluster, so the code path under test is the real one.
    The clinic filter is applied exactly as the default source applies it.
    """

    def source(clinic_id, limit=200):
        rows = (
            service_client.table("interaction_log")
            .select("*")
            .eq("target_type", "highlight")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
        ) or []
        if clinic_id is None:
            return rows
        members = {
            r["id"]
            for r in (
                service_client.table("profiles")
                .select("id")
                .eq("clinic_id", clinic_id)
                .execute()
                .data
                or []
            )
        }
        return [r for r in rows if r.get("user_id") in members]

    set_interaction_source(source)
    yield
    set_interaction_source(None)


def _log_interaction(client, *, user_id, role, action, target_id, keywords, topic):
    return (
        client.table("interaction_log")
        .insert(
            {
                "user_id": user_id,
                "user_role": role,
                "action_type": action,
                "target_type": "highlight",
                "target_id": target_id,
                # Metadata only: topic and keywords, never patient text.
                "target_metadata": {"keywords": keywords, "topic": topic},
            }
        )
        .execute()
    )


class TestLearningLoopChangesScores:
    """Pinning a topic must raise the priority of similar content next cycle."""

    async def test_pinning_a_topic_raises_future_scores_for_similar_content(
        self, wire_scorer_to_db, service_client, sample_highlights
    ):
        # A topic with no interaction history in this clinic.
        candidate = "Patient reports worsening peripheral neuropathy and numbness in both feet"

        before = await compute_importance_score(
            candidate, risk_level="medium", clinic_id=CLINIC_1
        )

        # A clinician repeatedly engages with this topic.
        target = sample_highlights[0]["id"]
        for action in ("pin", "accept", "manual_highlight"):
            _log_interaction(
                service_client,
                user_id=USERS["clinician"][0],
                role="clinician",
                action=action,
                target_id=target,
                keywords=["neuropathy", "numbness", "peripheral", "feet"],
                topic="neuropathy",
            )

        after = await compute_importance_score(
            candidate, risk_level="medium", clinic_id=CLINIC_1
        )

        assert after > before, (
            f"Score did not increase after pinning similar content "
            f"(before={before:.3f}, after={after:.3f})"
        )

    async def test_rejecting_a_topic_lowers_future_scores(
        self, wire_scorer_to_db, service_client, sample_highlights
    ):
        candidate = "Routine reminder that the annual influenza vaccination is available"

        before = await compute_importance_score(
            candidate, risk_level="low", clinic_id=CLINIC_1
        )

        target = sample_highlights[0]["id"]
        for _ in range(3):
            _log_interaction(
                service_client,
                user_id=USERS["clinician"][0],
                role="clinician",
                action="reject",
                target_id=target,
                keywords=["influenza", "vaccination", "routine", "reminder"],
                topic="vaccination",
            )

        after = await compute_importance_score(
            candidate, risk_level="low", clinic_id=CLINIC_1
        )

        assert after < before, (
            f"Score did not decrease after repeated rejection "
            f"(before={before:.3f}, after={after:.3f})"
        )

    async def test_learning_does_not_cross_clinic_boundary(
        self, wire_scorer_to_db, service_client, sample_highlights
    ):
        """
        Clinic A's engagement must not shift Clinic B's scores.

        This is the tenant boundary applied to the learning signal. The previous
        implementation accepted a patient_id, ignored it, and pooled the 200 most
        recent interactions across every clinic.
        """
        candidate = "Escalating albuminuria with declining filtration rate on repeat testing"

        baseline_clinic_2 = await compute_importance_score(
            candidate, risk_level="high", clinic_id=CLINIC_2
        )

        target = sample_highlights[0]["id"]
        for action in ("pin", "accept", "accept"):
            _log_interaction(
                service_client,
                user_id=USERS["clinician"][0],  # Nightingale Family Clinic
                role="clinician",
                action=action,
                target_id=target,
                keywords=["albuminuria", "filtration", "declining", "escalating"],
                topic="renal_function",
            )

        after_clinic_1 = await compute_importance_score(
            candidate, risk_level="high", clinic_id=CLINIC_1
        )
        after_clinic_2 = await compute_importance_score(
            candidate, risk_level="high", clinic_id=CLINIC_2
        )

        assert after_clinic_1 > baseline_clinic_2, "Clinic 1 should have learned from its own pins"
        assert after_clinic_2 == baseline_clinic_2, (
            f"Clinic 2's score moved ({baseline_clinic_2:.3f} -> {after_clinic_2:.3f}) "
            "because of Clinic 1's activity — learning crossed a tenant boundary"
        )

    async def test_unseen_topic_receives_neutral_prior(self, wire_scorer_to_db):
        """No signal must neither promote nor bury a topic."""
        a = await compute_importance_score(
            "Entirely unrelated administrative scheduling housekeeping item",
            risk_level="medium",
            clinic_id=CLINIC_1,
        )
        b = await compute_importance_score(
            "Different unrelated administrative scheduling housekeeping item",
            risk_level="medium",
            clinic_id=CLINIC_1,
        )
        assert a == b, "Two unseen topics at the same risk level should score identically"

    async def test_batch_score_reorders_by_learned_priority(
        self, wire_scorer_to_db, service_client, sample_highlights
    ):
        """
        The observable outcome the brief asks for: after learning, a highlight on
        the pinned topic outranks one that was repeatedly dismissed.
        """
        target = sample_highlights[0]["id"]
        for action in ("pin", "accept", "manual_highlight"):
            _log_interaction(
                service_client, user_id=USERS["clinician"][0], role="clinician",
                action=action, target_id=target,
                keywords=["potassium", "hyperkalemia", "electrolyte"], topic="electrolytes",
            )
        for _ in range(3):
            _log_interaction(
                service_client, user_id=USERS["clinician"][0], role="clinician",
                action="dismiss", target_id=target,
                keywords=["parking", "administrative", "housekeeping"], topic="admin",
            )

        candidates = [
            {"content_snippet": "Rising potassium with hyperkalemia risk on current electrolyte panel",
             "risk_level": "medium"},
            {"content_snippet": "Administrative housekeeping note regarding parking arrangements",
             "risk_level": "medium"},
        ]
        scored = await batch_score(candidates, clinic_id=CLINIC_1)

        clinical, admin = scored[0]["importance_score"], scored[1]["importance_score"]
        assert clinical > admin, (
            f"Learned priority did not reorder suggestions "
            f"(clinical={clinical:.3f}, admin={admin:.3f})"
        )
