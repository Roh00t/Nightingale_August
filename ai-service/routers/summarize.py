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

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from services.auth import CallerIdentity, require_caller, require_roles

from services.llm import generate_summary
from uuid import uuid4

from services.llm import MODEL_ID
from services.provenance import scribe_session_pointer
from services.supabase_writer import (
    AccessDenied,
    SupabaseUnavailable,
    insert_system_timeline_entry,
    resolve_care_note,
)
from services.prompt_integrity import (
    clean_fence_residue,
    fence_residue,
    scan_request,
)
from services.redaction import cleanup_redaction_map, de_redact, redact

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["summarize"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class TimelineEntry(BaseModel):
    """A single care note or timeline entry."""

    content: str = Field(
        ...,
        max_length=20_000,
        description="The text content of the entry",
    )
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
        max_length=200,
        description="Timeline entries to summarize",
    )
    patient_context: str = Field(
        default="",
        max_length=2_000,
        description=(
            "Optional patient context (diagnosis, age range). UNTRUSTED INPUT: "
            "this field is redacted on the same path as entry content and is "
            "fenced as data before it reaches the model. It previously went to "
            "the provider unredacted, so the wording here is deliberate - do "
            "not describe it as a place to put identifying detail."
        ),
    )
    file_to_timeline: bool = Field(
        default=False,
        description=(
            "File the summary as a system-authored timeline entry. Off by "
            "default so existing callers are unchanged."
        ),
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
    timeline_entry_id: str | None = Field(default=None)
    filed: bool = Field(default=False)


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
        401: {"description": "Missing or invalid bearer token"},
        422: {"description": "Validation error in request body"},
        500: {"description": "Internal server error during summarization"},
        503: {"description": "LLM service temporarily unavailable"},
    },
)
async def summarize(
    request: SummarizeRequest,
    caller: CallerIdentity = Depends(require_roles("clinician", "staff", "admin")),
) -> SummarizeResponse:
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

        # patient_context took a different path to the model than every other
        # byte in this request: it was passed through unredacted and prepended
        # to the FRONT of the user prompt, ahead of the instruction. That made
        # it both a PHI egress path around Presidio and the cleanest injection
        # vector in the service. It is redacted here so it travels the same
        # pipeline as entry content, and its map id joins the same cleanup loop.
        redacted_context = ""
        if request.patient_context:
            redacted_context, ctx_map = redact(request.patient_context)
            redaction_map_ids.append(ctx_map.id)

        # Advisory only. scan_request never blocks and never edits: it reports
        # that something in the input is SHAPED like an instruction to the
        # model. An empty verdict is not a statement that nothing is there.
        integrity = scan_request(redacted_entries, redacted_context)
        if integrity:
            logger.warning(
                "Possible prompt injection in care_note_id=%s fields=%s",
                request.care_note_id,
                integrity.get("injection_signal_fields"),
            )

        # Step 2: Generate summary from redacted content
        try:
            llm_result = await generate_summary(
                redacted_entries,
                patient_context=redacted_context,
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

        # --- File server-side, or not at all -------------------------------
        #
        # The browser used to write this row itself, and it could not write a
        # correct one. It sent author_role='system' with author_id = the signed-in
        # clinician and no provenance_pointer — a machine-attributed entry signed
        # by a human, with nothing tying the text to what produced it. The
        # database accepted it, because RLS only requires author_id = auth.uid().
        #
        # A user JWT genuinely cannot produce the right row: the correct shape is
        # author_role='system' with author_id NULL, which no INSERT policy admits
        # (and should not — a session that could write it could forge an entry
        # attributed to the AI scribe). So the write moves here, behind the
        # service-role key, with the tenant check re-applied by hand. Same
        # conclusion, and the same fix, as the ambient-capture path.
        entry_id: str | None = None
        if request.file_to_timeline:
            try:
                resolve_care_note(
                    request.care_note_id, caller_clinic_id=caller.clinic_id
                )
            except SupabaseUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except AccessDenied as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

            # The model echoing our fence substitution back is high-signal:
            # clinical text never contains it, because the only thing that
            # writes it is our own forgery-neutralisation. Record that it
            # happened BEFORE cleaning, or a cleaned string is indistinguishable
            # from one that was never affected.
            if fence_residue(patient_summary):
                integrity = {**integrity, "fence_residue_in_output": True}
                logger.warning(
                    "Model echoed fenced content for care_note_id=%s",
                    request.care_note_id,
                )
                patient_summary = clean_fence_residue(patient_summary)

            entry = insert_system_timeline_entry(
                care_note_id=request.care_note_id,
                entry_type="ai_doctor_consult_summary",
                content_text=patient_summary,
                provenance_pointer=scribe_session_pointer(
                    session_id=f"summary-{uuid4().hex[:12]}",
                    ai_model=MODEL_ID,
                ),
                metadata={
                    "capture": "summarize_endpoint",
                    "requested_by": caller.user_id,
                    "requested_by_role": caller.role,
                    "source_entry_count": len(request.entries),
                    **integrity,
                },
                risk_level="info",
            )
            entry_id = entry["id"]

        return SummarizeResponse(
            care_note_id=request.care_note_id,
            highlights=highlights,
            changes_since_last_visit=changes,
            care_plan_score=int(llm_result.get("care_plan_score", 50)),
            care_plan_items=care_plan_items,
            patient_summary=patient_summary,
            timeline_entry_id=entry_id,
            filed=entry_id is not None,
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
