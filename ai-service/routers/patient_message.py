"""
Patient message drafting endpoint for the Nightingale AI service.

POST /api/ai/draft-patient-message
- Receives care note entries for a patient
- Redacts PHI before sending to the LLM
- Generates a family-friendly message using generate_patient_summary
- De-redacts the response before returning to the caller
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from services.llm import generate_patient_summary
from services.redaction import cleanup_redaction_map, de_redact, redact

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["patient_message"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class TimelineEntry(BaseModel):
    content: str = Field(..., description="The text content of the entry")
    entry_type: str = Field(default="note")
    created_at: str | None = Field(default=None)
    entry_id: str | None = Field(default=None)


class DraftPatientMessageRequest(BaseModel):
    care_note_id: str = Field(..., description="ID of the care note")
    entries: list[TimelineEntry] = Field(
        ...,
        min_length=1,
        description="Timeline entries to base the message on",
    )
    patient_name: str | None = Field(
        default=None,
        description="Optional patient name for personalization",
    )
    author_role: str | None = Field(
        default="clinician",
        description="Role of the person drafting the message (clinician or staff)",
    )


class DraftPatientMessageResponse(BaseModel):
    care_note_id: str
    draft_message: str = Field(default="")
    key_points: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/draft-patient-message",
    response_model=DraftPatientMessageResponse,
    summary="Draft a patient-facing message",
    description=(
        "Generates an AI-drafted message suitable for sending to a patient, "
        "using the family_update summary type for compassionate, jargon-free language."
    ),
)
async def draft_patient_message(
    request: DraftPatientMessageRequest,
) -> DraftPatientMessageResponse:
    logger.info(
        "Draft patient message for care_note_id=%s with %d entries",
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
            })

        # Step 2: Generate family-friendly summary
        try:
            llm_result = await generate_patient_summary(
                redacted_entries,
                summary_type="family_update",
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

        # Step 3: De-redact
        draft_message = llm_result.get("summary", "")
        key_points = llm_result.get("key_points", [])

        for map_id in redaction_map_ids:
            draft_message = de_redact(draft_message, map_id)
            key_points = [
                de_redact(kp, map_id) if isinstance(kp, str) else kp
                for kp in key_points
            ]

        return DraftPatientMessageResponse(
            care_note_id=request.care_note_id,
            draft_message=draft_message,
            key_points=key_points,
        )

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception("Unexpected error in draft-patient-message endpoint")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Message drafting failed: {exc}",
        ) from exc

    finally:
        for map_id in redaction_map_ids:
            cleanup_redaction_map(map_id)
