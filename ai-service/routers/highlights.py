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
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from services.auth import CallerIdentity, require_caller, require_roles

from services.importance import batch_score
from services.safety.confidence import apply_abstention, assess_confidence
from services.safety.extraction import verify_quote
from services.safety.feedback import apply_importance_floor
from services.safety.risk_rules import RiskLevel, assess_risk
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
    provenance_pointer: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Discriminated pointer to the source: "
            "{source_type, source_id, span:{from,to}}"
        ),
    )

    # --- Clinical safety layer -------------------------------------------
    # Three distinct quantities, never collapsed: importance_score is queue
    # position, confidence_score is reliability, risk_level is severity.
    confidence_score: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Measured confidence: 0.50 agreement + 0.35 verification + 0.15 rules",
    )
    confidence_band: str | None = Field(
        default=None, description="high (>=0.85) | medium (0.60-0.84) | low (<0.60)",
    )
    risk_floor: str | None = Field(
        default=None, description="Level the deterministic rules required",
    )
    model_risk: str | None = Field(
        default=None, description="Level the model proposed. final = max(floor, proposal)",
    )
    abstained: bool = Field(
        default=False, description="Confidence below threshold; withheld for review",
    )
    safety_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Triggered rules, confidence components, extraction verdict",
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
        401: {"description": "Missing or invalid bearer token"},
        422: {"description": "Validation error in request body"},
        500: {"description": "Internal server error during highlight extraction"},
        503: {"description": "LLM service temporarily unavailable"},
    },
)
async def highlights(
    request: HighlightsRequest,
    caller: CallerIdentity = Depends(require_roles("clinician", "staff", "admin")),
) -> HighlightsResponse:
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

        # Step 4: Apply self-learning importance scoring.
        # Scoped to the caller's clinic: learning must never cross a tenant
        # boundary, and the caller's clinic comes from their verified JWT rather
        # than from the request body, which a client could forge.
        scored_highlights = await batch_score(
            raw_highlights,
            clinic_id=caller.clinic_id,
        )

        # Step 5: De-redact, then run the clinical safety layer.
        #
        # Order matters. Extraction is verified against the ORIGINAL entry text
        # (not the redacted form), because that is what a clinician will read
        # when they click through. A claim that is not a verbatim span of a
        # source entry is dropped here and never reaches the glance view.
        source_by_index = {i + 1: e.content for i, e in enumerate(request.entries)}
        entry_id_by_index = {i + 1: (e.entry_id or "") for i, e in enumerate(request.entries)}

        candidates: list[dict[str, Any]] = []
        rejected_unverifiable = 0

        for h in scored_highlights:
            snippet = h.get("content_snippet", "")
            risk_reason = h.get("risk_reason", "")
            for map_id in redaction_map_ids:
                snippet = de_redact(snippet, map_id)
                risk_reason = de_redact(risk_reason, map_id)

            pointer = str(h.get("provenance_pointer", ""))
            match = re.search(r"Entry\s+(\d+)", pointer)
            index = int(match.group(1)) if match else None
            source_text = source_by_index.get(index or -1, "")

            verdict, span_from, span_to = verify_quote(snippet, source_text)
            if not verdict.accepted:
                # Log length and verdict only — a rejected quote may still be PHI.
                rejected_unverifiable += 1
                logger.warning(
                    "Dropped unverifiable highlight (%d chars, %s) against entry %s",
                    len(snippet), verdict.value, index,
                )
                continue

            # Deterministic floor. The model proposed a level; the rules decide
            # the minimum. final = max(floor, proposal).
            risk = assess_risk(source_text[span_from:span_to] or snippet,
                               model_proposal=h.get("risk_level", "medium"))

            confidence = assess_confidence(
                snippet,
                # No ensemble here: a single generation pass yields the neutral
                # agreement prior rather than a fabricated certainty.
                samples=(),
                verified=True,
                verbatim=verdict.value == "exact",
                rule_supported=bool(risk.triggered),
            )

            importance, floored = apply_importance_floor(
                float(h.get("importance_score", 0.5)), risk.level
            )

            candidates.append({
                "content_snippet": source_text[span_from:span_to] or snippet,
                "risk_reason": risk_reason,
                "risk_level": risk.label,
                "importance_score": importance,
                "provenance_pointer": {
                    "source_type": "timeline_entry",
                    "source_id": entry_id_by_index.get(index or -1, ""),
                    "span": {"from": span_from, "to": span_to},
                },
                "confidence": confidence,
                "confidence_score": confidence.score,
                "confidence_band": confidence.band.value,
                "risk_floor": risk.floor.label,
                "model_risk": risk.model_proposal.label,
                "abstained": confidence.abstained,
                "safety_metadata": {
                    "triggered_rules": [
                        {"name": r.name, "rationale": r.rationale} for r in risk.triggered
                    ],
                    "confidence_components": confidence.components,
                    "extraction_verdict": verdict.value,
                    "importance_floor_applied": floored,
                },
            })

        # Abstention. Low-confidence claims are withheld for review rather than
        # guessed — except critical findings, which surface flagged, because
        # silently withholding a possible anaphylaxis is the worse failure.
        outcome = apply_abstention(candidates)

        result_highlights: list[Highlight] = []
        for c in outcome.surfaced:
            meta = dict(c["safety_metadata"])
            if c.get("unverified"):
                meta["unverified"] = True
            result_highlights.append(
                Highlight(
                    content_snippet=c["content_snippet"],
                    risk_reason=c["risk_reason"],
                    risk_level=c["risk_level"],
                    importance_score=c["importance_score"],
                    provenance_pointer=c["provenance_pointer"],
                    confidence_score=c["confidence_score"],
                    confidence_band=c["confidence_band"],
                    risk_floor=c["risk_floor"],
                    model_risk=c["model_risk"],
                    abstained=False,
                    safety_metadata=meta,
                )
            )

        logger.info(
            "Safety layer: %d surfaced, %d withheld (abstention), %d rejected as unverifiable",
            len(outcome.surfaced), len(outcome.withheld), rejected_unverifiable,
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
