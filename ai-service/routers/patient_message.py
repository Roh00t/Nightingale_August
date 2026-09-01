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

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from services.auth import CallerIdentity, require_caller, require_roles

from services.llm import generate_patient_summary
from services.redaction import cleanup_redaction_map, de_redact, redact
from services.messaging import queue_delivery
from services.safety.patient_gate import finalize_patient_message
from services.supabase_writer import (
    AccessDenied,
    SupabaseUnavailable,
    fetch_grounding_sources,
    get_profile,
    insert_patient_visible_entry,
    retract_patient_message,
    resolve_care_note,
)

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
    caller: CallerIdentity = Depends(require_caller),
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


# ---------------------------------------------------------------------------
# Send — the maker-checker gate, and the only path to a patient-visible entry
# ---------------------------------------------------------------------------


class SendPatientMessageRequest(BaseModel):
    care_note_id: str = Field(..., description="Care note the message belongs to")
    draft: str = Field(
        ...,
        min_length=1,
        description=(
            "The FINAL text as the clinician is sending it, including any manual "
            "edits. Not the AI's original draft — the edited text is what the "
            "patient will read, so the edited text is what must be checked."
        ),
    )


class SendPatientMessageResponse(BaseModel):
    entry_id: str
    message: str = Field(description="The sent text, with the approval attribution appended")
    verdict: str


@router.post(
    "/send-patient-message",
    response_model=SendPatientMessageResponse,
    summary="Screen and send a patient-facing message",
    description=(
        "Runs the maker-checker gate over the final draft and, only if it passes, "
        "files the patient-visible timeline entry. Returns 422 with the offending "
        "tokens if the draft is ungrounded or contains a prohibited speech act."
    ),
)
async def send_patient_message(
    request: SendPatientMessageRequest,
    caller: CallerIdentity = Depends(require_roles("clinician", "admin")),
) -> SendPatientMessageResponse:
    """
    Screen, then write. Both here, in that order, in one call.

    The gate used to exist only as a module with tests; nothing called it. A
    clinician pressed Send and the browser inserted straight into
    `timeline_entries`, so an edit made after the draft came back — the exact
    moment a dose can turn into 100000000mg — reached the patient unchecked.

    Two properties this arrangement has that a client-side check does not:

      * The sources are read here, from the record. If the caller supplied them,
        a fabricated dose could be sent as its own grounding and pass.
      * The write happens only on the passing branch of the same call. A check
        the browser performs before its own insert is advice, and any request
        made outside the UI skips it.

    Staff are excluded by `require_roles`: approving patient-facing clinical
    content is a clinician speech act, matching APPROVER_ROLES in the gate.
    """
    try:
        care_note = resolve_care_note(
            request.care_note_id, caller_clinic_id=caller.clinic_id
        )
    except SupabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AccessDenied as exc:
        # Same shape as "not found": do not confirm the id exists to an outsider.
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    sources = fetch_grounding_sources(care_note["id"])

    message, result = finalize_patient_message(
        request.draft,
        sources,
        approver_id=caller.user_id,
        approver_role=caller.role,
        approver_name=caller.display_name,
    )

    if message is None:
        # 422, not 400: the request is well-formed, its content is not sendable.
        # The offending tokens are returned so the UI can point at them rather
        # than saying "blocked" and leaving the clinician to hunt.
        logger.warning(
            "Patient message blocked for care_note_id=%s verdict=%s",
            request.care_note_id,
            result.verdict.value,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "verdict": result.verdict.value,
                "message": result.message,
                "ungrounded_terms": result.ungrounded_terms,
                "prohibited_hits": result.prohibited_hits,
            },
        )

    try:
        entry = insert_patient_visible_entry(
            care_note_id=care_note["id"],
            author_id=caller.user_id,
            author_role=caller.role,
            content_text=message,
            metadata={
                "direction": "outgoing",
                "ai_drafted": True,
                **result.to_metadata(),
            },
        )
    except SupabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # Record the delivery attempt (Audit 11). Writing the timeline entry means
    # the message exists in the record; it says nothing about the patient having
    # received it. Those are separate facts and are now stored separately.
    #
    # Failure here must not fail the send: the entry is already written and the
    # patient can read it in the portal. Losing the delivery trace is a
    # degradation of visibility, not of care, so it is logged and swallowed.
    try:
        patient = get_profile(care_note["patient_id"])
        phone = (patient or {}).get("phone_e164")
        if phone:
            await queue_delivery(
                clinic_id=caller.clinic_id,
                profile_id=care_note["patient_id"],
                channel="whatsapp",
                destination=phone,
                care_note_id=care_note["id"],
                entry_id=entry["id"],
                body=message,
            )
        else:
            # No reachable number. Worth logging loudly: the message is in the
            # portal, which a patient who cannot log in will never open.
            logger.warning(
                "No phone on file for patient %s - message %s is portal-only",
                care_note["patient_id"], entry["id"],
            )
    except Exception:
        logger.exception("Could not record delivery for entry %s", entry["id"])

    logger.info(
        "Patient message sent for care_note_id=%s by %s",
        request.care_note_id,
        caller.role,
    )
    return SendPatientMessageResponse(
        entry_id=entry["id"], message=message, verdict=result.verdict.value
    )


# ---------------------------------------------------------------------------
# Retraction — the correction half of maker-checker
# ---------------------------------------------------------------------------


class RetractPatientMessageRequest(BaseModel):
    care_note_id: str
    entry_id: str = Field(..., description="The sent patient message to withdraw")
    reason: str = Field(
        ...,
        min_length=10,
        description=(
            "Why it is being withdrawn. Shown to the patient verbatim, so it has "
            "to make sense to them."
        ),
    )


class RetractPatientMessageResponse(BaseModel):
    retracted_entry_id: str
    notice_entry_id: str


@router.post(
    "/retract-patient-message",
    response_model=RetractPatientMessageResponse,
    summary="Withdraw a patient message and notify the patient",
)
async def retract_patient_message_endpoint(
    request: RetractPatientMessageRequest,
    caller: CallerIdentity = Depends(require_roles("clinician", "admin")),
) -> RetractPatientMessageResponse:
    """
    Withdraw a message already sent to a patient.

    A gate on sending is only half of maker-checker. The other half is what
    happens when something wrong gets through anyway — and without a retraction
    path the honest answer was "nothing", which makes the approval step carry
    weight it cannot support.

    Restricted to clinician and admin, matching who may approve a send. Staff
    cannot withdraw clinical advice they were not permitted to issue.

    The reason has a minimum length because it is shown to the patient verbatim.
    A one-word reason produces a notice that alarms without informing.
    """
    try:
        care_note = resolve_care_note(
            request.care_note_id, caller_clinic_id=caller.clinic_id
        )
    except SupabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AccessDenied as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        result = retract_patient_message(
            entry_id=request.entry_id,
            care_note_id=care_note["id"],
            retracted_by=caller.user_id,
            retracted_by_role=caller.role,
            reason=request.reason.strip(),
        )
    except AccessDenied as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SupabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    logger.info(
        "Patient message %s retracted by %s on care_note %s",
        request.entry_id, caller.role, request.care_note_id,
    )
    return RetractPatientMessageResponse(**result)
