"""
Provenance pointer construction — one shape family, used everywhere.

Red-team item 4: `test_highlight_provenance.py` asserts every highlight's
provenance_pointer carries `source_type` and `source_id` and resolves to a real
timeline entry, while the scribe spec describes a pointer carrying
`session_id` / `ai_model` / `recording_duration_sec`. Those are two different
link types, and writing them ad hoc in each endpoint is how they drift apart.

They are reconciled here as a discriminated union keyed on `source_type`:

  scribe_session   an AI-scribed timeline entry -> the recording it came from
    {"source_type": "scribe_session", "session_id": str,
     "ai_model": str, "recording_duration_sec": int}

  timeline_entry   a highlight -> the entry and character span it came from
    {"source_type": "timeline_entry", "source_id": uuid,
     "span": {"from": int, "to": int}}

Every AI write goes through these builders, so `source_type` is always present
and a consumer can branch on it without guessing.
"""

from __future__ import annotations

from typing import Any, Literal

SOURCE_TYPE_SCRIBE_SESSION = "scribe_session"
SOURCE_TYPE_TIMELINE_ENTRY = "timeline_entry"

InteractionType = Literal["doctor_consult", "nurse_consult", "patient_session"]

# Interaction type -> timeline_entries.entry_type. The values on the right are
# constrained by a CHECK in 001_foundation.sql; an unmapped key would be
# rejected by the database, so the mapping is validated before any write.
ENTRY_TYPE_BY_INTERACTION: dict[str, str] = {
    "doctor_consult": "ai_doctor_consult_summary",
    "nurse_consult": "ai_nurse_consult_summary",
    "patient_session": "ai_patient_session_summary",
}


def entry_type_for(interaction_type: str) -> str:
    """Map an interaction type to its entry_type, or raise for an unknown one."""
    try:
        return ENTRY_TYPE_BY_INTERACTION[interaction_type]
    except KeyError:
        raise ValueError(
            f"Unknown interaction_type {interaction_type!r}. "
            f"Expected one of: {', '.join(sorted(ENTRY_TYPE_BY_INTERACTION))}"
        ) from None


def scribe_session_pointer(
    *, session_id: str, ai_model: str, recording_duration_sec: int | None = None
) -> dict[str, Any]:
    """Provenance for an AI-scribed timeline entry: which recording produced it."""
    pointer: dict[str, Any] = {
        "source_type": SOURCE_TYPE_SCRIBE_SESSION,
        "session_id": session_id,
        "ai_model": ai_model,
    }
    if recording_duration_sec is not None:
        pointer["recording_duration_sec"] = int(recording_duration_sec)
    return pointer


def timeline_entry_pointer(
    *, source_id: str, span_from: int, span_to: int
) -> dict[str, Any]:
    """Provenance for a highlight: which entry and character span it came from."""
    if span_from < 0 or span_to < span_from:
        raise ValueError(f"Invalid span [{span_from}:{span_to}]")
    return {
        "source_type": SOURCE_TYPE_TIMELINE_ENTRY,
        "source_id": source_id,
        "span": {"from": int(span_from), "to": int(span_to)},
    }


def locate_span(haystack: str, needle: str) -> tuple[int, int]:
    """
    Find `needle` in `haystack`, returning a character span.

    Highlights come back from the model as quoted snippets, which may differ
    from the source in whitespace or trailing punctuation. Falls back through
    progressively looser matches; returns (0, 0) only if nothing matches, which
    callers treat as "span unknown" rather than fabricating offsets.
    """
    if not needle or not haystack:
        return (0, 0)

    idx = haystack.find(needle)
    if idx >= 0:
        return (idx, idx + len(needle))

    lowered = haystack.lower().find(needle.lower())
    if lowered >= 0:
        return (lowered, lowered + len(needle))

    # Longest leading prefix of the snippet that still occurs in the source.
    trimmed = needle.strip().rstrip(".,;:!?")
    while len(trimmed) > 12:
        idx = haystack.lower().find(trimmed.lower())
        if idx >= 0:
            return (idx, idx + len(trimmed))
        trimmed = trimmed[: int(len(trimmed) * 0.8)]

    return (0, 0)


# ---------------------------------------------------------------------------
# Quote fingerprinting — Audit 16
# ---------------------------------------------------------------------------


def normalise_quote(quote: str) -> str:
    """
    Canonical form of a supporting quote, for hashing.

    Whitespace is collapsed and case is folded because neither carries clinical
    meaning: a clinician reflowing a paragraph or fixing capitalisation has not
    changed what the note says, and a highlight that reported "Source Modified"
    after a cosmetic edit would train people to ignore the warning. Everything
    else is preserved — punctuation and digits especially, since "10mg" and
    "1.0mg" must hash differently.
    """
    return " ".join(quote.split()).casefold()


def quote_hash(quote: str) -> str:
    """
    sha256 of the normalised quote.

    Hashing the quote rather than the whole entry is deliberate. An entry is
    often long and edited repeatedly; hashing all of it would invalidate every
    highlight derived from it whenever any unrelated sentence changed, and the
    resulting noise makes the signal worthless. The quote is the span the claim
    actually rests on, so a change to it is exactly the case a clinician needs
    to be told about.
    """
    import hashlib

    return hashlib.sha256(normalise_quote(quote).encode("utf-8")).hexdigest()
