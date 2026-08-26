"""
Clinical contradiction detection across authors.

The organisers' hint: "Conflict detection. Scope this for allergies,
medications, dosages, etc because human-human contradictions do exist between
clinician and nurse and staff notes."

This is a different problem from services/conflict.py. That module resolves
competing EDITS to the same section — one wins, deterministically. This module
detects competing CLAIMS about the same clinical fact, and deliberately does not
resolve them.

The distinction matters. If Dr A wrote 50mg and Nurse B wrote 500mg, the system
has no basis to decide which is correct, and picking one would manufacture false
certainty about a dosing decision. So it surfaces the delta with both verbatim
quotes and their authors, and asks a human. Silence would be worse; a guess
would be dangerous.

Detection is deterministic regex over the stored text — no model involved — so
it cannot hallucinate a contradiction that is not there, and it produces the
same answer on every run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class ConflictClass(str, Enum):
    """Contradiction categories, ordered by how badly they can hurt someone."""

    ALLERGY = "allergy"          # highest: an allergy contradiction can kill
    DOSAGE = "dosage"
    MEDICATION = "medication"
    VITAL = "vital"

    @property
    def severity(self) -> str:
        return "critical" if self is ConflictClass.ALLERGY else "high"


_UNIT = r"(?:mg|mcg|µg|g|ml|units?|iu)"

# Dose statements: "Lisinopril 10mg", "Metformin 1000 mg BID".
_DOSE = re.compile(
    rf"\b([A-Z][a-z]{{3,}}(?:ol|in|ide|ine|pril|artan|statin|azole|mycin|cillin|formin|pam|done))\b"
    rf"\s*(?:at|to|-)?\s*(\d+(?:\.\d+)?)\s*({_UNIT})\b",
    re.IGNORECASE,
)

# Positive allergy assertions.
_ALLERGY = re.compile(
    r"\ballerg(?:y|ic|ies)\s+(?:to\s+)?([A-Za-z][A-Za-z\- ]{2,30}?)\b(?=[.,;)]|\s+(?:and|with|but)\b|$)",
    re.IGNORECASE,
)

# Explicit denials — "not allergic to penicillin", "no known allergies".
_NO_ALLERGY = re.compile(
    r"\b(?:no\s+known\s+(?:drug\s+)?allergies|not\s+allergic\s+to\s+([A-Za-z][A-Za-z\- ]{2,30}?)\b)",
    re.IGNORECASE,
)

_VITALS = {
    "blood_pressure": re.compile(r"\bBP\s*:?\s*(\d{2,3}\s*/\s*\d{2,3})", re.IGNORECASE),
    "heart_rate": re.compile(r"\bHR\s*:?\s*(\d{2,3})\b", re.IGNORECASE),
    "a1c": re.compile(r"\b(?:A1C|HbA1c)\s*:?\s*(\d+(?:\.\d+)?)\s*%?", re.IGNORECASE),
}


@dataclass
class Assertion:
    """One clinical claim, tied to who said it and the exact words they used."""

    entity: str
    value: str
    conflict_class: ConflictClass
    author_id: str | None
    author_role: str
    entry_id: str
    quote: str
    timestamp: Any = None

    @property
    def normalized_value(self) -> str:
        return re.sub(r"\s+", "", self.value.lower())


@dataclass
class ClinicalConflict:
    """A contradiction, presented for human resolution — never auto-resolved."""

    entity: str
    conflict_class: ConflictClass
    assertions: list[Assertion] = field(default_factory=list)

    @property
    def severity(self) -> str:
        return self.conflict_class.severity

    @property
    def requires_human_resolution(self) -> bool:
        """Always true. The system reports the delta; a clinician decides."""
        return True

    @property
    def distinct_values(self) -> list[str]:
        seen: list[str] = []
        for a in self.assertions:
            if a.value not in seen:
                seen.append(a.value)
        return seen

    def describe(self) -> str:
        parts = [
            f"{a.author_role} noted {a.value!r}" for a in self.assertions
        ]
        return f"{self.conflict_class.value} conflict on {self.entity}: " + " vs ".join(parts)

    def to_metadata(self) -> dict[str, Any]:
        """
        Audit payload. Carries the quotes on purpose — a clinician resolving a
        dosing contradiction must see the exact wording of both claims. This is
        internal-only and never enters the patient-facing path.
        """
        return {
            "conflict_class": self.conflict_class.value,
            "entity": self.entity,
            "severity": self.severity,
            "requires_human_resolution": True,
            "claims": [
                {
                    "author_role": a.author_role,
                    "author_id": a.author_id,
                    "entry_id": a.entry_id,
                    "value": a.value,
                    "quote": a.quote,
                    "timestamp": a.timestamp,
                }
                for a in self.assertions
            ],
        }


def _quote_around(text: str, start: int, end: int, pad: int = 60) -> str:
    """The surrounding sentence fragment, so the claim is readable in context."""
    return text[max(0, start - pad) : min(len(text), end + pad)].strip()


def extract_assertions(entry: dict[str, Any]) -> list[Assertion]:
    """Pull medication, allergy and vital claims out of one timeline entry."""
    text = entry.get("content_text") or ""
    if not text:
        return []

    common = {
        "author_id": entry.get("author_id"),
        "author_role": entry.get("author_role", "unknown"),
        "entry_id": entry.get("id", ""),
        "timestamp": entry.get("created_at"),
    }
    found: list[Assertion] = []

    for m in _DOSE.finditer(text):
        drug, amount, unit = m.group(1), m.group(2), m.group(3)
        found.append(Assertion(
            entity=drug.lower(), value=f"{amount}{unit.lower()}",
            conflict_class=ConflictClass.DOSAGE,
            quote=_quote_around(text, m.start(), m.end()), **common,
        ))

    for m in _NO_ALLERGY.finditer(text):
        allergen = (m.group(1) or "any").strip().lower()
        found.append(Assertion(
            entity=f"allergy:{allergen}", value="none",
            conflict_class=ConflictClass.ALLERGY,
            quote=_quote_around(text, m.start(), m.end()), **common,
        ))

    denied_spans = [(m.start(), m.end()) for m in _NO_ALLERGY.finditer(text)]
    for m in _ALLERGY.finditer(text):
        # Skip a positive match that sits inside an explicit denial.
        if any(s <= m.start() < e for s, e in denied_spans):
            continue
        allergen = m.group(1).strip().lower()
        found.append(Assertion(
            entity=f"allergy:{allergen}", value="present",
            conflict_class=ConflictClass.ALLERGY,
            quote=_quote_around(text, m.start(), m.end()), **common,
        ))

    for name, pattern in _VITALS.items():
        for m in pattern.finditer(text):
            found.append(Assertion(
                entity=name, value=m.group(1).strip(),
                conflict_class=ConflictClass.VITAL,
                quote=_quote_around(text, m.start(), m.end()), **common,
            ))

    return found


def detect_conflicts(
    entries: Iterable[dict[str, Any]],
    *,
    include_same_author: bool = False,
) -> list[ClinicalConflict]:
    """
    Find entities asserted with different values across entries.

    By default only cross-author disagreements are reported. One author
    revising their own note over time is a correction, not a contradiction, and
    reporting it would be noise — which is exactly how alert fatigue starts.

    Vitals are excluded from conflict reporting entirely: a blood pressure
    differing between April and October is the timeline working as intended, not
    a disagreement.
    """
    assertions: list[Assertion] = []
    for entry in entries:
        assertions.extend(extract_assertions(entry))

    grouped: dict[tuple[str, ConflictClass], list[Assertion]] = {}
    for a in assertions:
        if a.conflict_class is ConflictClass.VITAL:
            continue  # measurements change legitimately over time
        grouped.setdefault((a.entity, a.conflict_class), []).append(a)

    conflicts: list[ClinicalConflict] = []
    for (entity, cls), group in grouped.items():
        if len({a.normalized_value for a in group}) < 2:
            continue
        if not include_same_author and len({a.author_id for a in group}) < 2:
            continue
        conflicts.append(ClinicalConflict(
            entity=entity, conflict_class=cls,
            assertions=sorted(group, key=lambda a: str(a.timestamp or "")),
        ))

    # Allergy contradictions first — they are the ones that kill people.
    order = {ConflictClass.ALLERGY: 0, ConflictClass.DOSAGE: 1,
             ConflictClass.MEDICATION: 2, ConflictClass.VITAL: 3}
    conflicts.sort(key=lambda c: (order[c.conflict_class], c.entity))
    return conflicts
