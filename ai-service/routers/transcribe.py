"""
Ambient consult capture.

POST /api/ai/transcribe

Audio in, structured clinical summary out. The order is fixed and the safety
properties depend on it:

    audio bytes
      -> size / type gate        reject before anything metered runs
      -> Scribe v2 (or mock)     diarized, speaker-labelled
      -> redaction.py            PHI stripped BEFORE any LLM sees the text
      -> structuring LLM         redacted dialogue only
      -> de-redact + verify      no placeholder survives into the record

Two things are deliberate.

The size and type checks run before transcription, not after. Transcription is
the metered step, so validating afterwards would spend credits to discover that
the upload was never acceptable.

Speaker labels are preserved through redaction. "I've been dizzy" means
something different from the clinician than from the patient, and provenance
back to a segment depends on the label surviving. The redactor is asked to
remove names, not the structure of the dialogue.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from services.auth import CallerIdentity, require_roles
from services.llm import generate_patient_summary
from services.provenance import (
    ENTRY_TYPE_BY_INTERACTION,
    entry_type_for,
    scribe_session_pointer,
)
from services.supabase_writer import (
    AccessDenied,
    SupabaseUnavailable,
    insert_system_timeline_entry,
    resolve_care_note,
)
from services.redaction import (
    assert_no_residual_placeholders,
    cleanup_redaction_map,
    de_redact,
    redact,
    validate_and_repair_placeholders,
)
from uuid import uuid4

from services.transcription import (
    ACCEPTED_AUDIO_TYPES,
    MAX_AUDIO_BYTES,
    TranscriptionUnavailable,
    transcribe,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["transcribe"])

# Read in bounded chunks so an oversized upload is rejected without ever being
# held in memory in full.
_CHUNK = 64 * 1024


class TranscriptSegmentOut(BaseModel):
    speaker: str
    text: str
    start: float | None = None
    end: float | None = None
    confidence: float | None = None


class TranscribeResponse(BaseModel):
    """Structured output of one ambient capture."""

    interaction_type: str = Field(..., description="patient_session | doctor_consult | nurse_consult")
    entry_type: str = Field(..., description="timeline_entries.entry_type this maps to")

    summary: str = Field(default="", description="Clinical summary, de-redacted")
    key_points: list[str] = Field(default_factory=list)

    # Speaker-labelled and PHI-free. This is what the LLM actually received, so
    # it is safe to return and is the honest artefact to audit.
    redacted_transcript: str = Field(default="")
    # Segment text is redacted too. The summary is de-redacted because it
    # becomes the clinical record; the transcript is a working artefact, and
    # there is no reason to ship raw identifiers twice in one response.
    segments: list[TranscriptSegmentOut] = Field(default_factory=list)
    speakers: list[str] = Field(default_factory=list)

    transcription: dict[str, Any] = Field(default_factory=dict)
    redaction: dict[str, Any] = Field(default_factory=dict)

    # Set when care_note_id was supplied and the entry was filed server-side.
    # None means "not requested" — the caller asked for a summary only.
    timeline_entry_id: str | None = Field(default=None)
    filed: bool = Field(
        default=False,
        description="Whether the summary was written to the timeline.",
    )


async def _read_capped(upload: UploadFile) -> bytes:
    """
    Read the upload, refusing anything over the cap.

    Streams in chunks and aborts as soon as the limit is passed, so a hostile
    or accidental large upload cannot exhaust memory on the way to a 413.
    """
    buffer = bytearray()
    while True:
        chunk = await upload.read(_CHUNK)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > MAX_AUDIO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(
                    f"Audio exceeds the {MAX_AUDIO_BYTES // (1024 * 1024)}MB limit. "
                    "Recordings are capped at 120 seconds; re-record a shorter clip."
                ),
            )
    return bytes(buffer)


@router.post(
    "/transcribe",
    response_model=TranscribeResponse,
    summary="Transcribe and structure an ambient consult recording",
    description=(
        "Accepts an audio upload, produces a diarized transcript, redacts PHI, and "
        "returns a structured clinical summary. Uses a deterministic mock transcript "
        "unless BOTH ?live=true and ELEVENLABS_LIVE_ENABLED=true are set."
    ),
    responses={
        400: {"description": "Empty audio or unsupported content type"},
        401: {"description": "Missing or invalid bearer token"},
        403: {"description": "Role may not use this capture mode"},
        413: {"description": "Audio exceeds the 5MB limit"},
        503: {"description": "Live transcription requested but unavailable"},
    },
)
async def transcribe_audio(
    audio: UploadFile = File(..., description="Recording from MediaRecorder"),
    interaction_type: str = Query(
        default="patient_session",
        description=f"One of: {', '.join(sorted(ENTRY_TYPE_BY_INTERACTION))}",
    ),
    live: bool = Query(
        default=False,
        description=(
            "Request a metered ElevenLabs call. Ignored unless the deployment also "
            "sets ELEVENLABS_LIVE_ENABLED=true."
        ),
    ),
    care_note_id: str | None = Query(
        default=None,
        description=(
            "File the summary to this care note as a system-authored entry. "
            "Omit to receive the summary without writing anything."
        ),
    ),
    caller: CallerIdentity = Depends(require_roles("clinician", "staff", "admin", "patient")),
) -> TranscribeResponse:
    # --- 1. Validate the interaction type ------------------------------------
    try:
        entry_type = entry_type_for(interaction_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # A patient may only produce a patient-session capture. The brief scopes
    # patient voice capture to the patient view, and a role check is the only
    # place that can actually enforce it — the UI cannot.
    if caller.role == "patient" and interaction_type != "patient_session":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Patients may only record patient session captures.",
        )

    # --- 2. Gate on size and type BEFORE anything metered runs ---------------
    content_type = (audio.content_type or "").split(";")[0].strip().lower()
    if content_type and content_type not in ACCEPTED_AUDIO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported audio content type '{content_type}'. "
                f"Expected one of: {', '.join(sorted(ACCEPTED_AUDIO_TYPES))}."
            ),
        )

    audio_bytes = await _read_capped(audio)

    if not audio_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Audio file is empty. Nothing was recorded.",
        )

    # --- 3. Transcribe (mock unless both opt-ins are present) ----------------
    try:
        transcript = transcribe(
            audio_bytes, live=live, filename=audio.filename or "audio.webm"
        )
    except TranscriptionUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    if not transcript.segments:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No speech was detected in the recording.",
        )

    # --- 4. Redact BEFORE the LLM -------------------------------------------
    # Speaker labels are inside the redacted string on purpose: the summariser
    # needs the dialogue structure, and only the identifiers are removed.
    map_ids: list[str] = []
    try:
        redacted_text, rmap = redact(transcript.text)
        map_ids.append(rmap.id)

        # Nothing past this line has seen the raw transcript.
        try:
            llm_result = await generate_patient_summary(
                [{"content": redacted_text, "entry_type": entry_type, "created_at": ""}],
                summary_type=(
                    "family_update" if interaction_type == "patient_session" else "clinical_review"
                ),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=f"LLM unavailable: {exc}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=f"LLM returned bad JSON: {exc}") from exc

        # --- 5. Repair placeholders, then restore ---------------------------
        raw_summary = llm_result.get("summary", "")
        report = validate_and_repair_placeholders(raw_summary, rmap)
        if not report.ok:
            logger.error("Placeholder integrity failure on transcription: %s", report.unknown)
            raise HTTPException(
                status_code=500,
                detail=(
                    "The summary failed placeholder integrity validation and was discarded. "
                    f"Unknown tokens: {', '.join(report.unknown)}"
                ),
            )

        summary = de_redact(report.repaired_text, rmap.id)
        residual = assert_no_residual_placeholders(summary)
        if residual:
            raise HTTPException(
                status_code=500,
                detail=f"Residual placeholders after de-redaction: {', '.join(residual)}",
            )

        key_points: list[str] = []
        for point in llm_result.get("key_points", []):
            if not isinstance(point, str):
                continue
            point_report = validate_and_repair_placeholders(point, rmap)
            if point_report.ok:
                key_points.append(de_redact(point_report.repaired_text, rmap.id))

        # Pair each segment with its redacted line. transcript.text joins one
        # line per segment, and redaction is a same-length substitution over
        # that string, so splitting restores the alignment exactly — the text
        # returned is byte-for-byte what the LLM received.
        redacted_lines = redacted_text.split("\n")
        redacted_segments: list[TranscriptSegmentOut] = []
        for index, segment in enumerate(transcript.segments):
            line = redacted_lines[index] if index < len(redacted_lines) else ""
            # Strip the "Speaker N: " prefix; the label is its own field.
            _, _, body = line.partition(": ")
            redacted_segments.append(
                TranscriptSegmentOut(
                    speaker=segment.speaker,
                    text=body or line,
                    start=segment.start,
                    end=segment.end,
                    confidence=segment.confidence,
                )
            )

        logger.info(
            "Ambient capture: %s, %d segment(s), %d speaker(s), %d entities redacted, source=%s",
            interaction_type, len(transcript.segments), len(transcript.speakers),
            rmap.total_entities, transcript.source,
        )

        # --- 6. File to the timeline, server-side ---------------------------
        #
        # This MUST happen here rather than in the browser. Every INSERT policy
        # on timeline_entries requires `author_id = auth.uid()`, and an
        # AI-scribed entry carries author_role='system' with author_id=NULL, so
        # the write is impossible from a user JWT for EVERY role — clinician and
        # admin included, not just staff. That is the policy working correctly:
        # if a user session could write author_role='system', any user could
        # forge a note attributed to the AI scribe.
        #
        # So the write happens with the service-role key, which bypasses RLS,
        # and the tenant and ownership checks RLS would have applied are
        # re-implemented here by hand (guardrails.md S3).
        entry_id: str | None = None
        if care_note_id:
            try:
                care_note = resolve_care_note(
                    care_note_id, caller_clinic_id=caller.clinic_id
                )
            except SupabaseUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except AccessDenied as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

            # A patient may only file into their OWN care note. Clinic match
            # alone would let them write into a peer's record.
            if caller.role == "patient" and care_note.get("patient_id") != caller.user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Patients may only file captures to their own care note.",
                )

            entry = insert_system_timeline_entry(
                care_note_id=care_note_id,
                entry_type=entry_type,
                content_text=summary,
                provenance_pointer=scribe_session_pointer(
                    session_id=f"voice-{uuid4().hex[:12]}",
                    ai_model=transcript.model_id,
                ),
                metadata={
                    "capture": "ambient_voice",
                    "captured_by": caller.user_id,
                    "captured_by_role": caller.role,
                    **transcript.to_metadata(),
                },
                risk_level="info",
            )
            entry_id = entry["id"]
            logger.info(
                "Filed ambient capture %s to care note %s", entry_id, care_note_id
            )

        return TranscribeResponse(
            timeline_entry_id=entry_id,
            filed=entry_id is not None,
            interaction_type=interaction_type,
            entry_type=entry_type,
            summary=summary,
            key_points=key_points,
            # Redacted, never raw: safe to return and safe to audit.
            redacted_transcript=redacted_text,
            segments=redacted_segments,
            speakers=transcript.speakers,
            transcription=transcript.to_metadata(),
            redaction={
                "entity_counts": rmap.entity_counts,
                "total_entities": rmap.total_entities,
                "placeholders_repaired": len(report.recovered),
            },
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Ambient capture failed")
        raise HTTPException(status_code=500, detail=f"Transcription pipeline failed: {exc}") from exc
    finally:
        for map_id in map_ids:
            cleanup_redaction_map(map_id)
