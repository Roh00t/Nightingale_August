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
