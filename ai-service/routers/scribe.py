"""
AI Scribe ingestion.

POST /api/ai/scribe

Takes a consultation transcript, redacts PHI, summarises it, and writes the
result to timeline_entries as a system-authored entry with highlights that point
back to it.

Order matters and is not negotiable: redact BEFORE the LLM call, verify
placeholder integrity BEFORE de-redaction, and confirm no placeholder survives
de-redaction BEFORE anything is stored. A failure at any of those points aborts
the write rather than persisting partially-redacted clinical text.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from services.auth import CallerIdentity, require_roles
from services.llm import generate_highlights, generate_patient_summary
from services.provenance import (
    ENTRY_TYPE_BY_INTERACTION,
    entry_type_for,
    locate_span,
    scribe_session_pointer,
    timeline_entry_pointer,
)
from services.redaction import (
    assert_no_residual_placeholders,
    cleanup_redaction_map,
    de_redact,
    redact,
    validate_and_repair_placeholders,
)
from services.supabase_writer import (
    AccessDenied,
    SupabaseUnavailable,
    get_patient_display_name,
    insert_highlights,
    insert_system_timeline_entry,
    resolve_care_note,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["scribe"])

_RISK_LEVELS = {"critical", "high", "medium", "low", "info"}


class ScribeRequest(BaseModel):
    care_note_id: str = Field(..., description="Care note to attach the summary to")
    session_id: str = Field(..., description="Recording session identifier, for provenance")
    interaction_type: str = Field(
        ...,
        description=f"One of: {', '.join(sorted(ENTRY_TYPE_BY_INTERACTION))}",
    )
    transcript: str = Field(..., min_length=1, description="Raw consultation transcript")
    ai_model: str = Field(default="nightingale-scribe-v1")
    recording_duration_sec: int | None = Field(default=None, ge=0)
    generate_highlights: bool = Field(default=True)


class ScribeHighlight(BaseModel):
    id: str | None = None
    content_snippet: str
    risk_reason: str
    risk_level: str
    importance_score: float
    provenance_pointer: dict[str, Any]


class ScribeResponse(BaseModel):
    timeline_entry_id: str
    care_note_id: str
    entry_type: str
    summary: str
    provenance_pointer: dict[str, Any]
    highlights: list[ScribeHighlight] = Field(default_factory=list)
    redaction: dict[str, Any] = Field(default_factory=dict)


@router.post(
    "/scribe",
    response_model=ScribeResponse,
    summary="Ingest an AI-scribed consultation summary",
    responses={
        401: {"description": "Missing or invalid bearer token"},
        403: {"description": "Caller's role may not ingest scribe sessions"},
        404: {"description": "Care note not found in the caller's clinic"},
        422: {"description": "Validation error"},
        500: {"description": "Redaction integrity failure or write error"},
        503: {"description": "LLM or database unavailable"},
    },
)
async def scribe(
    request: ScribeRequest,
    caller: CallerIdentity = Depends(require_roles("clinician", "staff", "admin")),
) -> ScribeResponse:
    try:
        entry_type = entry_type_for(request.interaction_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Clinic scoping, applied by hand because the service-role key bypasses RLS.
    try:
        care_note = resolve_care_note(request.care_note_id, caller_clinic_id=caller.clinic_id)
    except SupabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AccessDenied as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    map_ids: list[str] = []
    try:
        # The patient's own name is the single most important thing to remove and
        # the thing NER is least reliable about, so pass it as an exact-match
        # deny-list entry rather than trusting the model to find it.
        known_names: list[str] = []
        patient_name = get_patient_display_name(care_note["patient_id"])
        if patient_name:
            known_names.append(patient_name)

        redacted_transcript, rmap = redact(request.transcript, extra_names=known_names)
        map_ids.append(rmap.id)

        # Nothing beyond this line has seen the raw transcript.
        try:
            summary_result = await generate_patient_summary(
                [{"content": redacted_transcript, "entry_type": entry_type, "created_at": ""}],
                summary_type="clinical_review",
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=f"LLM unavailable: {exc}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=f"LLM returned bad JSON: {exc}") from exc

        raw_summary = summary_result.get("summary", "")

        # Repair mangled placeholders, then refuse to continue if any token in
        # the response is not one we issued.
        report = validate_and_repair_placeholders(raw_summary, rmap)
        if not report.ok:
            logger.error(
                "Placeholder integrity failure for session %s: unknown=%s",
                request.session_id,
                report.unknown,
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    "AI response failed placeholder integrity validation; "
                    "nothing was written. Unknown tokens: "
                    f"{', '.join(report.unknown)}"
                ),
            )

        summary = de_redact(report.repaired_text, rmap.id)

        # Belt and braces: a placeholder surviving de-redaction would mean a raw
        # token landing in the clinical record.
        residual = assert_no_residual_placeholders(summary)
        if residual:
            raise HTTPException(
                status_code=500,
                detail=f"Residual placeholders after de-redaction: {', '.join(residual)}",
            )

        entry = insert_system_timeline_entry(
            care_note_id=request.care_note_id,
            entry_type=entry_type,
            content_text=summary,
            provenance_pointer=scribe_session_pointer(
                session_id=request.session_id,
                ai_model=request.ai_model,
                recording_duration_sec=request.recording_duration_sec,
            ),
            metadata={
                "ingested_by": caller.user_id,
                "interaction_type": request.interaction_type,
                "redacted_entity_counts": rmap.entity_counts,
            },
            risk_level="info",
        )

        highlights: list[ScribeHighlight] = []
        if request.generate_highlights:
            highlights = await _build_highlights(
                redacted_transcript=redacted_transcript,
                rmap_id=rmap.id,
                rmap=rmap,
                entry_id=entry["id"],
                care_note_id=request.care_note_id,
                summary=summary,
            )

        return ScribeResponse(
            timeline_entry_id=entry["id"],
            care_note_id=request.care_note_id,
            entry_type=entry_type,
            summary=summary,
            provenance_pointer=entry["provenance_pointer"],
            highlights=highlights,
            redaction={
                "entity_counts": rmap.entity_counts,
                "total_entities": rmap.total_entities,
                "placeholders_repaired": len(report.recovered),
            },
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Scribe ingestion failed for session %s", request.session_id)
        raise HTTPException(status_code=500, detail=f"Scribe ingestion failed: {exc}") from exc
    finally:
        for map_id in map_ids:
            cleanup_redaction_map(map_id)


async def _build_highlights(
    *,
    redacted_transcript: str,
    rmap_id: str,
    rmap: Any,
    entry_id: str,
    care_note_id: str,
    summary: str,
) -> list[ScribeHighlight]:
    """
    Extract highlights and anchor each one to a span in the stored entry text.

    Spans are located against the de-redacted summary, which is what actually
    got written, so a pointer's offsets address the text a reader will see.
    """
    try:
        raw = await generate_highlights(
            [{"content": redacted_transcript, "entry_type": "transcript", "created_at": ""}]
        )
    except Exception:
        logger.exception("Highlight generation failed; entry was still written")
        return []

    rows: list[dict[str, Any]] = []
    built: list[ScribeHighlight] = []

    for item in raw:
        snippet_raw = item.get("content_snippet", "")
        reason_raw = item.get("risk_reason", "")
        if not snippet_raw:
            continue

        snippet_report = validate_and_repair_placeholders(snippet_raw, rmap)
        reason_report = validate_and_repair_placeholders(reason_raw, rmap)
        if not snippet_report.ok or not reason_report.ok:
            logger.warning("Skipping highlight with corrupt placeholders: %r", snippet_raw)
            continue

        snippet = de_redact(snippet_report.repaired_text, rmap_id)
        reason = de_redact(reason_report.repaired_text, rmap_id)
        if assert_no_residual_placeholders(snippet) or assert_no_residual_placeholders(reason):
            logger.warning("Skipping highlight with residual placeholders")
            continue

        risk = str(item.get("risk_level", "medium")).lower()
        if risk not in _RISK_LEVELS:
            risk = "medium"
        score = max(0.0, min(1.0, float(item.get("importance_score", 0.5))))

        span_from, span_to = locate_span(summary, snippet)
        pointer = timeline_entry_pointer(
            source_id=entry_id, span_from=span_from, span_to=span_to
        )

        rows.append(
            {
                "care_note_id": care_note_id,
                "source_entry_id": entry_id,
                "content_snippet": snippet,
                "risk_reason": reason or "AI-identified clinical highlight",
                "risk_level": risk,
                "importance_score": score,
                "provenance_pointer": pointer,
                "created_by": "system",
            }
        )
        built.append(
            ScribeHighlight(
                content_snippet=snippet,
                risk_reason=reason or "AI-identified clinical highlight",
                risk_level=risk,
                importance_score=score,
                provenance_pointer=pointer,
            )
        )

    inserted = insert_highlights(rows)
    for built_row, stored in zip(built, inserted):
        built_row.id = stored.get("id")
    return built
