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
