"""
Test Highlight Provenance — verifies provenance tracking on AI highlights.

Tests that:
- Every AI-generated highlight has a provenance_pointer
- Each provenance_pointer resolves to a valid timeline entry
- Every highlight has a non-empty risk_reason
- Provenance pointers contain required fields (source_type, source_id)
- Highlights maintain referential integrity with source entries
"""

import pytest

pytestmark = pytest.mark.asyncio


class TestHighlightProvenance:
    """Test suite for highlight provenance tracking."""

    async def test_all_highlights_have_provenance(
        self, clinician_client, sample_highlights
    ):
        """Every highlight should have a provenance_pointer."""
        for highlight in sample_highlights:
            assert highlight["provenance_pointer"] is not None, (
                f"Highlight {highlight['id']} missing provenance_pointer"
            )

    async def test_provenance_pointer_has_required_fields(
        self, clinician_client, sample_highlights
    ):
        """Each provenance_pointer should have source_type and source_id."""
        for highlight in sample_highlights:
            provenance = highlight["provenance_pointer"]
            assert provenance is not None, f"Highlight {highlight['id']} has null provenance"
            assert "source_type" in provenance, (
                f"Highlight {highlight['id']} provenance missing source_type"
            )
            assert "source_id" in provenance, (
                f"Highlight {highlight['id']} provenance missing source_id"
            )

    async def test_provenance_resolves_to_valid_entry(
        self, clinician_client, sample_highlights
    ):
        """Each provenance_pointer should resolve to an existing timeline entry."""
        for highlight in sample_highlights:
            provenance = highlight["provenance_pointer"]
            if provenance is None:
                continue

            source_id = provenance.get("source_id")
            if not source_id:
                continue

            # Verify the source entry exists
            result = (
                clinician_client.table("timeline_entries")
                .select("id")
                .eq("id", source_id)
                .execute()
            )
            assert len(result.data) == 1, (
                f"Highlight {highlight['id']} provenance points to non-existent "
                f"entry {source_id}"
            )

    async def test_all_highlights_have_risk_reason(
        self, clinician_client, sample_highlights
    ):
        """Every highlight must have a non-empty risk_reason."""
        for highlight in sample_highlights:
            assert highlight["risk_reason"] is not None, (
                f"Highlight {highlight['id']} has null risk_reason"
            )
            assert highlight["risk_reason"].strip() != "", (
                f"Highlight {highlight['id']} has empty risk_reason"
            )

    async def test_all_highlights_have_risk_level(
        self, clinician_client, sample_highlights
    ):
        """Every highlight should have a valid risk_level."""
        valid_levels = {"critical", "high", "medium", "low", "info"}
        for highlight in sample_highlights:
            assert highlight["risk_level"] in valid_levels, (
                f"Highlight {highlight['id']} has invalid risk_level: "
                f"{highlight['risk_level']}"
            )

    async def test_highlights_have_importance_scores(
        self, clinician_client, sample_highlights
    ):
        """Every highlight should have an importance_score between 0 and 1."""
        for highlight in sample_highlights:
            score = highlight["importance_score"]
            assert 0.0 <= score <= 1.0, (
                f"Highlight {highlight['id']} importance_score {score} out of range"
            )

    async def test_source_entry_belongs_to_same_care_note(
        self, clinician_client, sample_care_note_id, sample_highlights
    ):
        """Source entries referenced by highlights should belong to the same care note."""
        for highlight in sample_highlights:
            if highlight["source_entry_id"] is None:
                continue

            result = (
                clinician_client.table("timeline_entries")
                .select("care_note_id")
                .eq("id", highlight["source_entry_id"])
                .single()
                .execute()
            )
            assert result.data["care_note_id"] == sample_care_note_id, (
                f"Highlight {highlight['id']} source entry belongs to different care note"
            )

    async def test_highlight_content_snippet_not_empty(
        self, clinician_client, sample_highlights
    ):
        """Every highlight should have a non-empty content_snippet."""
        for highlight in sample_highlights:
            assert highlight["content_snippet"] is not None, (
                f"Highlight {highlight['id']} has null content_snippet"
            )
            assert highlight["content_snippet"].strip() != "", (
                f"Highlight {highlight['id']} has empty content_snippet"
            )

    async def test_provenance_span_is_valid(
        self, clinician_client, sample_highlights
    ):
        """Provenance pointers with span should have valid from/to values."""
        for highlight in sample_highlights:
            provenance = highlight.get("provenance_pointer")
            if not provenance or "span" not in provenance:
                continue

            span = provenance["span"]
            assert "from" in span and "to" in span, (
                f"Highlight {highlight['id']} span missing from/to"
            )
            assert span["from"] >= 0, "Span 'from' should be non-negative"
            assert span["to"] > span["from"], "Span 'to' should be greater than 'from'"


# ===========================================================================
# Provenance schema — offline unit tests
# ===========================================================================
# The suite above needs a live seeded Supabase. These do not, so the provenance
# contract stays verifiable in any checkout.
#
# Red-team item 4: the scribe spec describes a pointer carrying session_id /
# ai_model / recording_duration_sec, while the assertions above require
# source_type and source_id resolving to a real timeline entry. Those are two
# distinct link types, reconciled in services/provenance.py as a discriminated
# union keyed on source_type. These tests pin that reconciliation down.

from services.provenance import (  # noqa: E402
    ENTRY_TYPE_BY_INTERACTION,
    SOURCE_TYPE_SCRIBE_SESSION,
    SOURCE_TYPE_TIMELINE_ENTRY,
    entry_type_for,
    locate_span,
    scribe_session_pointer,
    timeline_entry_pointer,
)


class TestProvenanceSchema:
    """The shape of every provenance_pointer the AI service writes."""

    async def test_scribe_pointer_carries_session_fields(self):
        pointer = scribe_session_pointer(
            session_id="sess-2026-02-01-alice-chen",
            ai_model="nightingale-scribe-v1",
            recording_duration_sec=1245,
        )
        assert pointer["source_type"] == SOURCE_TYPE_SCRIBE_SESSION
        assert pointer["session_id"] == "sess-2026-02-01-alice-chen"
        assert pointer["ai_model"] == "nightingale-scribe-v1"
        assert pointer["recording_duration_sec"] == 1245

    async def test_scribe_pointer_omits_absent_duration(self):
        """Duration is optional; an absent one is omitted, never null or zero."""
        pointer = scribe_session_pointer(session_id="s1", ai_model="m1")
        assert "recording_duration_sec" not in pointer

    async def test_highlight_pointer_matches_integration_contract(self):
        """
        The exact shape the suite above asserts on:
        {"source_type": "timeline_entry", "source_id": <uuid>, "span": {...}}
        """
        entry_id = "11111111-2222-3333-4444-555555555555"
        pointer = timeline_entry_pointer(source_id=entry_id, span_from=0, span_to=45)
        assert pointer["source_type"] == SOURCE_TYPE_TIMELINE_ENTRY
        assert pointer["source_id"] == entry_id
        assert pointer["span"] == {"from": 0, "to": 45}

    async def test_every_pointer_is_discriminated_by_source_type(self):
        """A consumer can always branch on source_type without guessing."""
        for pointer in (
            scribe_session_pointer(session_id="s", ai_model="m"),
            timeline_entry_pointer(source_id="e", span_from=1, span_to=2),
        ):
            assert "source_type" in pointer
            assert pointer["source_type"] in {
                SOURCE_TYPE_SCRIBE_SESSION,
                SOURCE_TYPE_TIMELINE_ENTRY,
            }

    async def test_invalid_span_is_rejected(self):
        """A reversed or negative span would produce an unresolvable pointer."""
        with pytest.raises(ValueError):
            timeline_entry_pointer(source_id="e", span_from=50, span_to=10)
        with pytest.raises(ValueError):
            timeline_entry_pointer(source_id="e", span_from=-1, span_to=10)

    async def test_all_three_interaction_types_map_to_valid_entry_types(self):
        """
        Each scribe interaction type maps to an entry_type the database CHECK
        in 001_foundation.sql accepts.
        """
        allowed = {
            "ai_doctor_consult_summary",
            "ai_nurse_consult_summary",
            "ai_patient_session_summary",
        }
        assert set(ENTRY_TYPE_BY_INTERACTION) == {
            "doctor_consult",
            "nurse_consult",
            "patient_session",
        }
        for interaction in ENTRY_TYPE_BY_INTERACTION:
            assert entry_type_for(interaction) in allowed

    async def test_unknown_interaction_type_is_rejected(self):
        """Fail before the write rather than on a database CHECK violation."""
        with pytest.raises(ValueError):
            entry_type_for("ai_physio_summary")


class TestSpanResolution:
    """locate_span anchors a highlight to characters in the stored entry text."""

    async def test_exact_snippet_resolves(self):
        source = "Patient reports dyspnea on exertion after climbing stairs."
        start, end = locate_span(source, "dyspnea on exertion")
        assert source[start:end] == "dyspnea on exertion"

    async def test_case_insensitive_fallback(self):
        source = "Patient reports Dyspnea On Exertion today."
        start, end = locate_span(source, "dyspnea on exertion")
        assert source[start:end].lower() == "dyspnea on exertion"

    async def test_trailing_punctuation_tolerated(self):
        """Models quote snippets with punctuation the source does not have."""
        source = "eGFR dropped to 45 from 58 in June and warrants review"
        start, end = locate_span(source, "eGFR dropped to 45 from 58 in June.")
        assert start == 0
        assert end > 12

    async def test_absent_snippet_reports_unknown_rather_than_guessing(self):
        """(0, 0) means 'span unknown' — never fabricated offsets."""
        assert locate_span("Nothing relevant here.", "completely unrelated text") == (0, 0)

    async def test_empty_inputs_are_safe(self):
        assert locate_span("", "x") == (0, 0)
        assert locate_span("x", "") == (0, 0)
