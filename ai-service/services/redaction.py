"""
PHI redaction pipeline using pure regex pattern matching.

Provides bidirectional redaction: PHI removal for LLM processing and
de-anonymization for restoring original content. Redaction maps are
kept server-side only and never exposed to clients.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compiled regex patterns for PHI detection
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Singapore NRIC
    ("SG_NRIC", re.compile(r"\b[STFGM]\d{7}[A-Z]\b")),
    # SG mobile
    ("PHONE_NUMBER", re.compile(r"\b[89]\d{7}\b")),
    # SG landline
    ("PHONE_NUMBER", re.compile(r"\b6\d{7}\b")),
    # SG phone with prefix
    ("PHONE_NUMBER", re.compile(r"\b\+65\s?[689]\d{7}\b")),
    # Medical Record Number
    ("MEDICAL_RECORD_NUMBER", re.compile(r"\bMRN[:\s-]?\d{6,10}\b", re.IGNORECASE)),
    # Email address
    ("EMAIL_ADDRESS", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")),
    # Credit card (basic 13-19 digit)
    ("CREDIT_CARD", re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{1,7}\b")),
    # IPv4 address
    ("IP_ADDRESS", re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")),
    # URL
    ("URL", re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)),
    # Date patterns (DD/MM/YYYY, MM/DD/YYYY, YYYY-MM-DD)
    ("DATE_TIME", re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")),
    ("DATE_TIME", re.compile(r"\b\d{4}-\d{2}-\d{2}\b")),
]


# ---------------------------------------------------------------------------
# Redaction map: stores the bidirectional mapping for a single request
# ---------------------------------------------------------------------------


@dataclass
class RedactionMap:
    """Server-side only mapping between original PHI and placeholders."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    forward: dict[str, str] = field(default_factory=dict)   # original -> placeholder
    reverse: dict[str, str] = field(default_factory=dict)    # placeholder -> original
    entity_counts: dict[str, int] = field(default_factory=dict)

    def add(self, original: str, entity_type: str) -> str:
        """Register an original value and return its placeholder."""
        if original in self.forward:
            return self.forward[original]

        count = self.entity_counts.get(entity_type, 0) + 1
        self.entity_counts[entity_type] = count
        placeholder = f"<{entity_type}_{count}>"

        self.forward[original] = placeholder
        self.reverse[placeholder] = original
        return placeholder

    @property
    def total_entities(self) -> int:
        return len(self.forward)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# In-memory store keyed by RedactionMap.id. In production, back this with
# Redis or an encrypted database table with TTL expiry.
_redaction_store: dict[str, RedactionMap] = {}


def redact(text: str) -> tuple[str, RedactionMap]:
    """
    Redact PHI from the given text using regex pattern matching.

    Returns:
        Tuple of (redacted_text, redaction_map). The redaction map is kept
        server-side and should never be sent to the client.
    """
    if not text or not text.strip():
        empty_map = RedactionMap()
        _redaction_store[empty_map.id] = empty_map
        return text, empty_map

    # Collect all matches with their spans
    matches: list[tuple[int, int, str, str]] = []  # (start, end, entity_type, matched_text)

    for entity_type, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            matches.append((m.start(), m.end(), entity_type, m.group()))

    if not matches:
        empty_map = RedactionMap()
        _redaction_store[empty_map.id] = empty_map
        return text, empty_map

    # Sort by start position descending so we can replace from the end
    # without shifting indices.
    matches.sort(key=lambda x: x[0], reverse=True)

    # De-duplicate overlapping spans: keep the longest (first encountered after sort)
    filtered: list[tuple[int, int, str, str]] = []
    for match in matches:
        overlaps = False
        for existing in filtered:
            if match[0] < existing[1] and match[1] > existing[0]:
                overlaps = True
                break
        if not overlaps:
            filtered.append(match)

    redaction_map = RedactionMap()

    redacted = text
    for start, end, entity_type, original_value in filtered:
        placeholder = redaction_map.add(original_value, entity_type)
        redacted = redacted[:start] + placeholder + redacted[end:]

    _redaction_store[redaction_map.id] = redaction_map

    logger.info(
        "Redacted %d entities (%s) from text of length %d",
        redaction_map.total_entities,
        ", ".join(f"{k}:{v}" for k, v in redaction_map.entity_counts.items()),
        len(text),
    )

    return redacted, redaction_map


def de_redact(redacted_text: str, map_id: str) -> str:
    """
    Restore original PHI values using a previously stored redaction map.

    Args:
        redacted_text: Text containing placeholders like <PERSON_1>.
        map_id: The ID of the RedactionMap created during redaction.

    Returns:
        Text with placeholders replaced by original values.

    Raises:
        KeyError: If the map_id is not found (expired or invalid).
    """
    redaction_map = _redaction_store.get(map_id)
    if redaction_map is None:
        raise KeyError(f"Redaction map '{map_id}' not found or has expired")

    result = redacted_text
    for placeholder, original in redaction_map.reverse.items():
        result = result.replace(placeholder, original)

    return result


def cleanup_redaction_map(map_id: str) -> bool:
    """Remove a redaction map from the store. Returns True if it existed."""
    return _redaction_store.pop(map_id, None) is not None
