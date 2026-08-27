"""
Service-role Supabase writes for system-authored records.

Red-team item 3: RLS in 001_foundation.sql admits a timeline_entries INSERT only
when `author_id = auth.uid()`. AI-scribed notes must carry
`author_role = 'system'` with `author_id = NULL`, which no user JWT can satisfy
— the insert fails with 42501. System ingestion therefore runs through the
service-role key, which bypasses RLS.

Guardrail S3: because RLS is bypassed here, every tenant and role check RLS
would have applied is re-implemented in this module. `resolve_care_note` is the
choke point — nothing writes to a care note without first resolving it and
confirming the caller's clinic matches.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_client: Any | None = None


class SupabaseUnavailable(RuntimeError):
    """Raised when service-role credentials are not configured."""


class AccessDenied(PermissionError):
    """Raised when a caller may not touch the requested care note."""


def get_service_client() -> Any:
    """Return a cached service-role client, or raise if unconfigured."""
    global _client
    if _client is not None:
        return _client

    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SupabaseUnavailable(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set for AI scribe ingestion"
        )

    from supabase import create_client

    _client = create_client(url, key)
    logger.info("Supabase service-role client initialised for system ingestion")
    return _client


def resolve_care_note(care_note_id: str, *, caller_clinic_id: str) -> dict[str, Any]:
    """
    Fetch a care note and confirm it belongs to the caller's clinic.

    This is the hand-written replacement for the clinic scoping that RLS would
    have enforced. Never write to a care note without calling this first.
    """
    client = get_service_client()
    resp = (
        client.table("care_notes")
        .select("id, clinic_id, patient_id")
        .eq("id", care_note_id)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    if not rows:
        raise AccessDenied(f"Care note {care_note_id} not found")

    note = rows[0]
    if note["clinic_id"] != caller_clinic_id:
        # Same message as "not found" on purpose: a caller outside the clinic
        # must not be able to probe which care note ids exist.
        raise AccessDenied(f"Care note {care_note_id} not found")
    return note


def get_profile(user_id: str) -> dict[str, Any] | None:
    """Look up a caller's profile (role + clinic) for authorization decisions."""
    client = get_service_client()
    resp = (
        client.table("profiles")
        .select("id, role, clinic_id, display_name")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None


def get_patient_display_name(patient_id: str) -> str | None:
    """Patient's name, fed to the redactor's deny-list as a precision layer."""
    profile = get_profile(patient_id)
    return profile.get("display_name") if profile else None


def insert_system_timeline_entry(
    *,
    care_note_id: str,
    entry_type: str,
    content_text: str,
    provenance_pointer: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    risk_level: str = "info",
) -> dict[str, Any]:
    """
    Insert an AI-scribed timeline entry as the system author.

    author_role is 'system' and author_id is NULL — the combination that user
    JWTs cannot write. visibility is always 'internal': raw AI-scribed notes are
    never patient-visible, and 001_foundation.sql additionally excludes these
    entry types from the patient SELECT policy by type, so a later mistake in
    this file still cannot expose one.
    """
    client = get_service_client()
    row = {
        "care_note_id": care_note_id,
        "author_role": "system",
        "author_id": None,
        "entry_type": entry_type,
        "content": {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": content_text}]}
            ],
        },
        "content_text": content_text,
        "provenance_pointer": provenance_pointer,
        "risk_level": risk_level,
        "visibility": "internal",
        "metadata": metadata or {},
    }
    resp = client.table("timeline_entries").insert(row).execute()
    if not resp.data:
        raise RuntimeError("Timeline entry insert returned no row")
    return resp.data[0]


def insert_highlights(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bulk-insert highlights. Returns the inserted rows."""
    if not rows:
        return []
    client = get_service_client()
    resp = client.table("highlights").insert(rows).execute()
    return resp.data or []


def fetch_grounding_sources(care_note_id: str) -> list[str]:
    """
    The text a patient-facing draft must be grounded against, read server-side.

    This is deliberately NOT taken from the request body. Grounding compares the
    draft against the record; if the caller supplied the record, the check would
    verify the draft against itself and a fabricated dose could be waved through
    by sending it as its own source. The whole gate turns on this read being
    authoritative.

    Archived entries are included: a dose the clinician tapered off last month is
    still a real number from the record, and excluding it would block a message
    that legitimately refers back to it.
    """
    client = get_service_client()
    resp = (
        client.table("timeline_entries")
        .select("content_text")
        .eq("care_note_id", care_note_id)
        .execute()
    )
    return [
        row["content_text"]
        for row in (resp.data or [])
        if row.get("content_text")
    ]


def insert_patient_visible_entry(
    *,
    care_note_id: str,
    author_id: str,
    author_role: str,
    content_text: str,
    entry_type: str = "instruction",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    File a patient-visible message that has passed the maker-checker gate.

    Written server-side with the service-role key, not from the browser. A
    clinician's own JWT *could* satisfy the RLS INSERT policy here — unlike an
    AI-scribed entry — so this is not about what RLS permits. It is about where
    the gate sits: if the browser performs the insert, the grounding check is
    advice the client may skip, and a request crafted outside the UI writes to
    the patient's record ungated. Routing the write through the same call that
    runs the gate removes both the bypass and the window between checking and
    writing.

    The caller's clinic has already been confirmed by resolve_care_note; the
    role allowlist by require_roles. author_id is the real approving clinician,
    never a sentinel — the patient is entitled to know who signed off.
    """
    client = get_service_client()
    row = {
        "care_note_id": care_note_id,
        "author_role": author_role,
        "author_id": author_id,
        "entry_type": entry_type,
        "content": {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": content_text}]}
            ],
        },
        "content_text": content_text,
        "risk_level": "info",
        "visibility": "patient_visible",
        "metadata": metadata or {},
    }
    resp = client.table("timeline_entries").insert(row).execute()
    if not resp.data:
        raise RuntimeError("Patient message insert returned no row")
    return resp.data[0]
