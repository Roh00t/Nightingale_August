"""
Highlights extraction endpoint for the Nightingale AI service.

POST /api/ai/highlights
- Receives care note entries
- Redacts PHI before LLM processing
- Extracts clinical highlights with risk assessment
- Applies self-learning importance scoring
- Returns ranked highlights with provenance
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from services.importance import batch_score
from services.llm import generate_highlights
from services.redaction import cleanup_redaction_map, de_redact, redact

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["highlights"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class HighlightEntry(BaseModel):
    """A single care note entry for highlight extraction."""

    content: str = Field(..., description="Text content of the entry")
    entry_type: str = Field(default="note", description="Type of entry")
    created_at: str | None = Field(default=None, description="ISO 8601 timestamp")
    entry_id: str | None = Field(default=None, description="Unique entry identifier")


class HighlightsRequest(BaseModel):
    """Request body for the highlights endpoint."""

    entries: list[HighlightEntry] = Field(
        ...,
        min_length=1,
        description="Care note entries to extract highlights from",
    )
    patient_id: str | None = Field(
        default=None,
        description="Patient ID for personalized importance scoring",
    )


class Highlight(BaseModel):
    """A single clinical highlight with risk assessment."""

    content_snippet: str = Field(..., description="Relevant excerpt from the note")
    risk_reason: str = Field(..., description="Clinical rationale for flagging")
    risk_level: str = Field(
        default="medium",
        description="Risk level: critical, high, medium, or low",
    )
    importance_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Composite importance score (0.0-1.0)",
    )
    provenance_pointer: str = Field(
        default="",
        description="Reference to the source entry for traceability",
    )


class HighlightsResponse(BaseModel):
    """Response from the highlights endpoint."""

    highlights: list[Highlight] = Field(default_factory=list)
    total_entries_analyzed: int = Field(default=0)
    risk_summary: dict[str, int] = Field(
        default_factory=dict,
        description="Count of highlights by risk level",
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/highlights",
    response_model=HighlightsResponse,
    summary="Extract clinical highlights",
    description=(
        "Analyzes care note entries to extract clinically significant highlights. "
        "Each highlight includes a risk assessment, importance score informed by "
        "historical clinician engagement, and a provenance pointer to the source entry."
    ),
    responses={
        422: {"description": "Validation error in request body"},
        500: {"description": "Internal server error during highlight extraction"},
        503: {"description": "LLM service temporarily unavailable"},
    },
)
async def highlights(request: HighlightsRequest) -> HighlightsResponse:
    """Extract and score clinical highlights from care note entries."""
    logger.info(
        "Highlights request with %d entries, patient_id=%s",
        len(request.entries),
        request.patient_id or "none",
    )

    redaction_map_ids: list[str] = []

    try:
        # Step 1: Redact PHI from each entry
        redacted_entries: list[dict[str, Any]] = []
        for entry in request.entries:
            redacted_text, rmap = redact(entry.content)
            redaction_map_ids.append(rmap.id)
            redacted_entries.append({
                "content": redacted_text,
                "entry_type": entry.entry_type,
                "created_at": entry.created_at or "",
                "entry_id": entry.entry_id or "",
            })

        # Step 2: Generate highlights from redacted content via LLM
        try:
            raw_highlights = await generate_highlights(redacted_entries)
        except RuntimeError as exc:
            logger.error("LLM service error during highlight extraction: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"LLM service temporarily unavailable: {exc}",
            ) from exc
        except ValueError as exc:
            logger.error("LLM response parsing error: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to parse LLM response: {exc}",
            ) from exc

        # Step 3: Enrich highlights with created_at from matching entries for scoring
        for h in raw_highlights:
            provenance = h.get("provenance_pointer", "")
            # Try to match "Entry N" to get created_at for recency scoring
            for i, entry in enumerate(request.entries):
                if f"Entry {i+1}" in provenance and entry.created_at:
                    h["created_at"] = entry.created_at
                    break

        # Step 4: Apply self-learning importance scoring
        scored_highlights = await batch_score(
            raw_highlights,
            patient_id=request.patient_id,
        )

        # Step 5: De-redact highlight snippets
        result_highlights: list[Highlight] = []
        for h in scored_highlights:
            snippet = h.get("content_snippet", "")
            risk_reason = h.get("risk_reason", "")

            # De-redact using all maps (a snippet might reference any entry)
            for map_id in redaction_map_ids:
                snippet = de_redact(snippet, map_id)
                risk_reason = de_redact(risk_reason, map_id)

            result_highlights.append(
                Highlight(
                    content_snippet=snippet,
                    risk_reason=risk_reason,
                    risk_level=h.get("risk_level", "medium"),
                    importance_score=h.get("importance_score", 0.5),
                    provenance_pointer=h.get("provenance_pointer", ""),
                )
            )

        # Sort by importance score descending
        result_highlights.sort(key=lambda h: h.importance_score, reverse=True)

        # Build risk summary
        risk_summary: dict[str, int] = {}
        for h in result_highlights:
            level = h.risk_level
            risk_summary[level] = risk_summary.get(level, 0) + 1

        return HighlightsResponse(
            highlights=result_highlights,
            total_entries_analyzed=len(request.entries),
            risk_summary=risk_summary,
        )

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception("Unexpected error in highlights endpoint")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Highlight extraction failed: {exc}",
        ) from exc

    finally:
        for map_id in redaction_map_ids:
            cleanup_redaction_map(map_id)
