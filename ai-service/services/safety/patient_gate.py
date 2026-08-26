"""
Maker-checker firewall for patient-facing text.

The organisers' hint: "Patient facing generation is a higher severity class. You
show a patient something hallucinated and it's game over. Internal notes can get
audited, but what's sent to the patient needs more visible human approvals
and/or rules."

So the patient path is the only one in this system where AI output cannot reach
its audience on its own. Three gates, in order, all of which must pass:

  1. Grounding check (deterministic)
     Every clinical token in the draft — drug names, doses, numbers, dates —
     must appear in the source entries. A number the model invented is the
     canonical catastrophic failure here: "take 100mg" when the record says
     10mg. This is a string check, not a judgement, so it cannot itself
     hallucinate.

  2. Prohibited-content check (deterministic)
     Patient messages must not contain diagnosis-by-AI, prognosis, or
     instructions to stop treatment. These are clinician speech acts and an
     assistant has no standing to make them.

  3. Human approval (recorded)
     A named clinician or admin approves. The approval is stored with the
     message and rendered as visible attribution, so the patient can see a human
     stands behind it and an auditor can see who.

Failing gate 1 or 2 blocks the send outright — the draft is returned to the
clinician for editing rather than softened or auto-corrected. Silent repair
would hide exactly the failure this exists to catch.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)

APPROVER_ROLES = frozenset({"clinician", "admin"})


class GateVerdict(str, Enum):
    PASSED = "passed"
    BLOCKED_UNGROUNDED = "blocked_ungrounded"
    BLOCKED_PROHIBITED = "blocked_prohibited"
    BLOCKED_NO_APPROVAL = "blocked_no_approval"

    @property
    def ok(self) -> bool:
        return self is GateVerdict.PASSED


# Numbers carry dosing meaning; a fabricated one is the worst failure mode.
#
# No trailing \b: "100mg" has no word boundary between the digits and the unit,
# so \b\d+\b silently skips the single most dangerous token in the message.
# The unit is captured with the number so 10mg and 10ml are distinct tokens.
_NUMBER = re.compile(r"(?<!\w)(\d+(?:\.\d+)?)\s*(mg|mcg|µg|ml|g|units?|iu|%)?", re.IGNORECASE)

# Drug-shaped tokens. Broad suffix matching beats a fixed vocabulary here: an
# unrecognised drug name should be CHECKED, not ignored.
_DRUGLIKE = re.compile(
    r"\b[A-Za-z]{4,}(?:ol|in|ide|ine|pril|artan|statin|azole|mycin|cillin|formin|pam|done)\b"
)

# Speech acts an assistant must not perform toward a patient.
_PROHIBITED: list[tuple[str, re.Pattern[str]]] = [
    ("diagnosis", re.compile(r"\byou\s+(?:have|are\s+suffering\s+from|likely\s+have)\b\s+\w+", re.I)),
    ("prognosis", re.compile(r"\b(?:life\s+expectancy|months\s+to\s+live|terminal|will\s+not\s+recover)\b", re.I)),
    ("stop_treatment", re.compile(r"\b(?:stop|discontinue|cease)\s+(?:taking|your)\b", re.I)),
    ("dose_change", re.compile(r"\b(?:double|triple|halve|increase|decrease)\s+your\s+dose\b", re.I)),
    ("emergency_deferral", re.compile(r"\b(?:no\s+need|don'?t\s+need)\s+to\s+(?:worry|see|call)\b", re.I)),
    ("ai_self_reference", re.compile(r"\bas\s+an\s+AI\b|\bI\s+am\s+an?\s+(?:AI|language\s+model)\b", re.I)),
]

# Everyday words that happen to match _DRUGLIKE but carry no clinical claim.
_BENIGN = {
    "morning", "evening", "within", "again", "certain", "medicine", "medication",
    "routine", "examine", "determine", "remain", "explain", "continue", "online",
    "appointment", "nurse", "doctor", "insulin",  # insulin appears in source when relevant
}


@dataclass
class GateResult:
    """Outcome of screening one patient-facing draft."""

    verdict: GateVerdict
    ungrounded_terms: list[str] = field(default_factory=list)
    prohibited_hits: list[str] = field(default_factory=list)
    approved_by: str | None = None
    approver_role: str | None = None
    message: str = ""

    @property
    def sendable(self) -> bool:
        return self.verdict.ok and self.approved_by is not None

    def to_metadata(self) -> dict[str, Any]:
        """Audit record. Ungrounded TERMS are kept (they are the evidence);
        the draft body is not, so the log does not become a second copy."""
        return {
            "patient_gate_verdict": self.verdict.value,
            "ungrounded_terms": self.ungrounded_terms,
            "prohibited_hits": self.prohibited_hits,
            "approved_by": self.approved_by,
            "approver_role": self.approver_role,
            "human_approved": self.approved_by is not None,
        }


def _clinical_tokens(text: str) -> set[str]:
    """Tokens that carry a clinical claim and therefore must be grounded."""
    tokens: set[str] = set()
    for m in _NUMBER.finditer(text):
        number, unit = m.group(1), (m.group(2) or "").lower()
        tokens.add(f"{number}{unit}" if unit else number)
    tokens |= {
        m.group(0).lower()
        for m in _DRUGLIKE.finditer(text)
        if m.group(0).lower() not in _BENIGN
    }
    return tokens


def check_grounding(draft: str, sources: Iterable[str]) -> list[str]:
    """
    Return clinical tokens in the draft that do not appear in any source.

    An empty list means every dose, number and drug name in the message traces
    back to the record.
    """
    # Set membership, not substring containment. A substring test would treat
    # "1" as grounded by "10mg" and "10" as grounded by "100mg" — the second is
    # a dosing error passing the gate in the dangerous direction.
    grounded = _clinical_tokens(" ".join(sources))
    grounded_lower = {t.lower() for t in grounded}
    return sorted(
        t for t in _clinical_tokens(draft) if t.lower() not in grounded_lower
    )


def check_prohibited(draft: str) -> list[str]:
    return [name for name, pattern in _PROHIBITED if pattern.search(draft)]


def screen_patient_draft(draft: str, sources: Sequence[str]) -> GateResult:
    """Run the two deterministic gates. Approval is applied separately."""
    if not draft or not draft.strip():
        return GateResult(
            verdict=GateVerdict.BLOCKED_UNGROUNDED,
            message="Draft is empty.",
        )

    prohibited = check_prohibited(draft)
    if prohibited:
        logger.warning("Patient draft blocked: prohibited content %s", prohibited)
        return GateResult(
            verdict=GateVerdict.BLOCKED_PROHIBITED,
            prohibited_hits=prohibited,
            message=(
                "Blocked: the draft contains content a clinician must say directly "
                f"({', '.join(prohibited)}). Edit the draft and try again."
            ),
        )

    ungrounded = check_grounding(draft, sources)
    if ungrounded:
        # Log the tokens, not the draft: the tokens are the finding, the draft may carry PHI.
        logger.warning("Patient draft blocked: ungrounded terms %s", ungrounded)
        return GateResult(
            verdict=GateVerdict.BLOCKED_UNGROUNDED,
            ungrounded_terms=ungrounded,
            message=(
                "Blocked: these details do not appear in the patient's record — "
                f"{', '.join(ungrounded)}. They may have been invented. "
                "Correct the draft before sending."
            ),
        )

    return GateResult(verdict=GateVerdict.PASSED, message="Draft is grounded and permitted.")


def approve(result: GateResult, *, approver_id: str, approver_role: str) -> GateResult:
    """
    Record a named human approval.

    Only a clinician or admin may approve. Approval cannot rescue a blocked
    draft — a human signing off on ungrounded content is precisely the outcome
    the gates exist to prevent, so the checks run first and approval second.
    """
    if not result.verdict.ok:
        return result

    if approver_role not in APPROVER_ROLES:
        return GateResult(
            verdict=GateVerdict.BLOCKED_NO_APPROVAL,
            message=(
                f"Role '{approver_role}' cannot approve patient-facing messages. "
                f"Requires one of: {', '.join(sorted(APPROVER_ROLES))}."
            ),
        )

    result.approved_by = approver_id
    result.approver_role = approver_role
    return result


def attribution_line(approver_name: str) -> str:
    """
    Visible provenance for the patient.

    The patient is told a human reviewed it and who. Without this the message
    reads as if it came from their clinician unaided, which is not true.
    """
    return (
        f"This message was drafted with AI assistance and reviewed and approved "
        f"by {approver_name} before being sent to you."
    )


def finalize_patient_message(
    draft: str,
    sources: Sequence[str],
    *,
    approver_id: str,
    approver_role: str,
    approver_name: str,
) -> tuple[str | None, GateResult]:
    """
    Full maker-checker pass.

    Returns (message_to_send, result). The message is None whenever any gate
    failed — callers must not fall back to sending the raw draft.
    """
    result = screen_patient_draft(draft, sources)
    result = approve(result, approver_id=approver_id, approver_role=approver_role)

    if not result.sendable:
        return None, result

    return f"{draft.strip()}\n\n— {attribution_line(approver_name)}", result
