"""
Summarization endpoint for the Nightingale AI service.

POST /api/ai/summarize
- Receives care note entries for a patient
- Redacts PHI before sending to the LLM
- Generates structured clinical summary via Groq
- De-redacts the response before returning to the caller
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from services.llm import generate_summary
from services.redaction import cleanup_redaction_map, de_redact, redact

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["summarize"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class TimelineEntry(BaseModel):
    """A single care note or timeline entry."""

    content: str = Field(..., description="The text content of the entry")
    entry_type: str = Field(
        default="note",
        description="Type of entry: note, vitals, medication, observation, task",
    )
    created_at: str | None = Field(
        default=None,
        description="ISO 8601 timestamp of when the entry was created",
    )
    entry_id: str | None = Field(
        default=None,
        description="Unique identifier for provenance tracking",
    )


class SummarizeRequest(BaseModel):
    """Request body for the summarize endpoint."""

    care_note_id: str = Field(..., description="ID of the care note or visit session")
    entries: list[TimelineEntry] = Field(
        ...,
        min_length=1,
        description="Timeline entries to summarize",
    )
    patient_context: str = Field(
        default="",
        description="Optional patient context (diagnosis, age range, etc.)",
    )


class CarePlanItem(BaseModel):
    """A single actionable care plan item."""

    item: str
    priority: str = Field(default="medium", pattern=r"^(high|medium|low)$")
    status: str = Field(default="new", pattern=r"^(new|ongoing|resolved)$")


class SummarizeResponse(BaseModel):
    """Response from the summarize endpoint."""

    care_note_id: str
    highlights: list[str] = Field(default_factory=list)
    changes_since_last_visit: list[str] = Field(default_factory=list)
    care_plan_score: int = Field(default=50, ge=0, le=100)
    care_plan_items: list[CarePlanItem] = Field(default_factory=list)
    patient_summary: str = Field(default="")


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/summarize",
    response_model=SummarizeResponse,
    summary="Summarize care notes",
    description=(
        "Accepts a set of timeline entries for a care session, redacts PHI, "
        "generates a structured clinical summary via LLM, and returns the "
        "de-redacted result."
    ),
    responses={
        422: {"description": "Validation error in request body"},
        500: {"description": "Internal server error during summarization"},
        503: {"description": "LLM service temporarily unavailable"},
    },
)
async def summarize(request: SummarizeRequest) -> SummarizeResponse:
    """Generate a clinical summary from care note timeline entries."""
    logger.info(
        "Summarize request for care_note_id=%s with %d entries",
        request.care_note_id,
        len(request.entries),
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

        # Step 2: Generate summary from redacted content
        try:
            llm_result = await generate_summary(
                redacted_entries,
                patient_context=request.patient_context,
            )
        except RuntimeError as exc:
            logger.error("LLM service error: %s", exc)
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

        # Step 3: De-redact the summary output so the caller gets real names back
        # The LLM may use placeholders like <PERSON_1> in its output.
        patient_summary = llm_result.get("patient_summary", "")
        highlights = llm_result.get("highlights", [])
        changes = llm_result.get("changes_since_last_visit", [])

        for map_id in redaction_map_ids:
            patient_summary = de_redact(patient_summary, map_id)
            highlights = [de_redact(h, map_id) if isinstance(h, str) else h for h in highlights]
            changes = [de_redact(c, map_id) if isinstance(c, str) else c for c in changes]

        # Parse care plan items
        raw_items = llm_result.get("care_plan_items", [])
        care_plan_items: list[CarePlanItem] = []
        for item in raw_items:
            if isinstance(item, dict):
                # De-redact the item text
                item_text = item.get("item", "")
                for map_id in redaction_map_ids:
                    item_text = de_redact(item_text, map_id)
                care_plan_items.append(
                    CarePlanItem(
                        item=item_text,
                        priority=item.get("priority", "medium"),
                        status=item.get("status", "new"),
                    )
                )

        return SummarizeResponse(
            care_note_id=request.care_note_id,
            highlights=highlights,
            changes_since_last_visit=changes,
            care_plan_score=int(llm_result.get("care_plan_score", 50)),
            care_plan_items=care_plan_items,
            patient_summary=patient_summary,
        )

    except HTTPException:
        raise  # Re-raise HTTP exceptions as-is

    except Exception as exc:
        logger.exception("Unexpected error in summarize endpoint")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Summarization failed: {exc}",
        ) from exc

    finally:
        # Cleanup redaction maps to prevent memory leaks
        for map_id in redaction_map_ids:
            cleanup_redaction_map(map_id)
