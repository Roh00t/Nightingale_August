"""
Clinical contradiction detection.

POST /api/ai/conflicts

Detection previously ran twice: once in Python (the tested reference) and once
in a hand-maintained TypeScript port so the UI could flag contradictions without
a round trip. Nothing enforced that the two stayed in lockstep — a change to one
would not fail a test in the other, and the two copies would drift silently
until they disagreed about whether a dosing contradiction existed. For a safety
control that is an unacceptable failure mode, so the port was deleted and this
endpoint is now the single implementation.

No LLM is involved. Detection is deterministic regex over the stored text, so
the result is reproducible and cannot hallucinate a contradiction. That also
means no redaction happens here: nothing leaves our own infrastructure, and
redaction exists to protect the boundary to Groq, not to obscure the record from
the care team that owns it.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from services.auth import CallerIdentity, require_caller, require_roles
from services.safety.clinical_conflict import detect_conflicts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["conflicts"])


class ConflictEntry(BaseModel):
    """One timeline entry to scan."""

    id: str = Field(..., description="Timeline entry id")
    author_id: str | None = Field(default=None)
    author_role: str = Field(default="unknown")
    content_text: str | None = Field(default=None)
    created_at: str | None = Field(default=None)


class ConflictsRequest(BaseModel):
    entries: list[ConflictEntry] = Field(default_factory=list)
    include_same_author: bool = Field(
        default=False,
        description=(
            "Report an author contradicting themselves. Off by default: one "
            "clinician revising their own dose over time is a correction, and "
            "flagging it is noise."
        ),
    )


class ConflictClaimOut(BaseModel):
    author_role: str
    author_id: str | None = None
    entry_id: str
    value: str
    quote: str
    timestamp: str | None = None
    agreed_by: int = 1


class ConflictOut(BaseModel):
    conflict_class: str
    entity: str
    severity: str
    requires_human_resolution: bool
    claims: list[ConflictClaimOut]


class ConflictsResponse(BaseModel):
    conflicts: list[ConflictOut] = Field(default_factory=list)
    entries_scanned: int = 0
    has_critical: bool = False


@router.post(
    "/conflicts",
    response_model=ConflictsResponse,
    summary="Detect clinical contradictions across authors",
    description=(
        "Deterministic detection of medication-dosage and allergy contradictions "
        "between different authors. Surfaces the delta with both verbatim quotes; "
        "never arbitrates."
    ),
    responses={
        401: {"description": "Missing or invalid bearer token"},
        422: {"description": "Validation error"},
    },
)
async def conflicts(
    request: ConflictsRequest,
    caller: CallerIdentity = Depends(require_roles("clinician", "staff", "admin")),
) -> ConflictsResponse:
    try:
        detected = detect_conflicts(
            [e.model_dump() for e in request.entries],
            include_same_author=request.include_same_author,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Conflict detection failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Conflict detection failed: {exc}",
        ) from exc

    out: list[ConflictOut] = []
    for c in detected:
        payload: dict[str, Any] = c.to_metadata()
        out.append(
            ConflictOut(
                conflict_class=payload["conflict_class"],
                entity=payload["entity"],
                severity=payload["severity"],
                requires_human_resolution=True,
                claims=[ConflictClaimOut(**claim) for claim in payload["claims"]],
            )
        )

    # Counts only — never the quotes, which carry clinical text.
    logger.info(
        "Conflict scan: %d entries, %d contradiction(s) for clinic %s",
        len(request.entries), len(out), caller.clinic_id,
    )

    return ConflictsResponse(
        conflicts=out,
        entries_scanned=len(request.entries),
        has_critical=any(c.severity == "critical" for c in out),
    )
