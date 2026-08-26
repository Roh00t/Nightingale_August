"""
PHI redaction endpoint for the Nightingale AI service.

POST /api/ai/redact
- Receives raw text containing potential PHI
- Runs it through the Presidio-based redaction pipeline
- Returns redacted text with entity count
- Redaction map is kept server-side and never exposed
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from services.redaction import cleanup_redaction_map, redact

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["redact"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class RedactRequest(BaseModel):
    """Request body for the redact endpoint."""

    text: str = Field(
        ...,
        min_length=1,
        description="Raw text that may contain PHI to be redacted",
    )
    cleanup_map: bool = Field(
        default=True,
        description=(
            "Whether to immediately discard the redaction map after processing. "
            "Set to False if you need de-redaction later (advanced use only)."
        ),
    )


class EntityBreakdown(BaseModel):
    """Count of entities detected per type."""

    entity_type: str
    count: int


class RedactResponse(BaseModel):
    """Response from the redact endpoint."""

    redacted_text: str = Field(..., description="Text with PHI replaced by placeholders")
    entity_count: int = Field(
        default=0,
        ge=0,
        description="Total number of PHI entities detected and redacted",
    )
    entities_by_type: list[EntityBreakdown] = Field(
        default_factory=list,
        description="Breakdown of entity counts by type",
    )
    redaction_map_id: str | None = Field(
        default=None,
        description=(
            "ID of the stored redaction map for de-redaction. "
            "Only returned when cleanup_map=False."
        ),
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/redact",
    response_model=RedactResponse,
    summary="Redact PHI from text",
    description=(
        "Detects and replaces Protected Health Information (PHI) in the provided text "
        "using Microsoft Presidio with spaCy NER. Supports Singapore-specific formats "
        "including NRIC numbers, local phone numbers, and medical record numbers. "
        "Entities are replaced with deterministic placeholders like <PERSON_1>, <PHONE_1>."
    ),
    responses={
        422: {"description": "Validation error in request body"},
        500: {"description": "Internal server error during redaction"},
    },
)
async def redact_text(request: RedactRequest) -> RedactResponse:
    """Redact PHI from the provided text."""
    logger.info("Redact request received, text length=%d", len(request.text))

    try:
        redacted_text, redaction_map = redact(request.text)

        entities_by_type = [
            EntityBreakdown(entity_type=entity_type, count=count)
            for entity_type, count in redaction_map.entity_counts.items()
        ]

        # Sort by count descending for readability
        entities_by_type.sort(key=lambda e: e.count, reverse=True)

        map_id: str | None = None
        if request.cleanup_map:
            cleanup_redaction_map(redaction_map.id)
        else:
            map_id = redaction_map.id

        logger.info(
            "Redaction complete: %d entities found across %d types",
            redaction_map.total_entities,
            len(redaction_map.entity_counts),
        )

        return RedactResponse(
            redacted_text=redacted_text,
            entity_count=redaction_map.total_entities,
            entities_by_type=entities_by_type,
            redaction_map_id=map_id,
        )

    except Exception as exc:
        logger.exception("Error during text redaction")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Redaction failed: {exc}",
        ) from exc
