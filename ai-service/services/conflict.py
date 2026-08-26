"""
Deterministic conflict resolution for concurrent edits.

The brief: "If clinician edits conflict with prior AI/patient memory (including
AI-scribed notes), the clinician's entry takes precedence, OR the system must
flag the conflict for review." And test_concurrent_edits requires "a
deterministic resolution strategy" when two roles touch the same section.

Deterministic means: the same set of edits always resolves the same way,
regardless of arrival order, which machine ran it, or dict iteration order. That
matters because two clients resolving the same conflict independently must reach
the same answer, or they diverge.

The ordering is a strict total order, applied in sequence until one edit wins:

  1. Role authority   clinician > staff > patient > system
  2. Recency          later timestamp wins within the same authority
  3. Identity         lexicographically smallest edit id wins on an exact tie

Step 3 exists solely to remove the last source of nondeterminism. Two edits with
the same role and the same microsecond are vanishingly rare, but "vanishingly
rare" is not "impossible", and an unresolved tie would let two clients disagree.

A losing edit is NEVER discarded. It is returned in `superseded` so it can be
preserved as a version and surfaced for review — the clinician needs to see what
the AI claimed, not just that it lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

# Higher wins. 'system' is lowest by design: an AI-scribed claim never overrides
# a human, which is the trust property the whole product rests on.
ROLE_AUTHORITY: dict[str, int] = {
    "clinician": 40,
    "admin": 30,
    "staff": 20,
    "patient": 10,
    "system": 0,
}


def _parse_ts(value: Any) -> datetime:
    """Parse a timestamp to an aware datetime; unparseable sorts oldest."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    # Unix epoch, not datetime.min: datetime.min.timestamp() overflows on some
    # platforms, and an unparseable timestamp should simply sort oldest.
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Edit:
    """One candidate edit to a section."""

    edit_id: str
    section: str
    author_role: str
    author_id: str | None
    content: str
    timestamp: Any = None

    @property
    def authority(self) -> int:
        return ROLE_AUTHORITY.get(self.author_role, 0)

    @property
    def when(self) -> datetime:
        return _parse_ts(self.timestamp)


@dataclass
class Resolution:
    """Outcome for one section."""

    section: str
    winner: Edit
    superseded: list[Edit] = field(default_factory=list)
    conflict: bool = False
    reason: str = ""

    @property
    def requires_review(self) -> bool:
        """
        True when a human should look at this.

        A clinician overriding an AI note is routine and resolves silently. Two
        humans of different authority disagreeing is not routine — that gets
        flagged even though the ordering already picked a winner.
        """
        if not self.conflict:
            return False
        return any(e.author_role != "system" for e in self.superseded)


def resolve_section(edits: Iterable[Edit]) -> Resolution:
    """
    Resolve competing edits to a single section.

    Raises ValueError on an empty input or on edits spanning multiple sections —
    silently resolving a mixed batch would produce a wrong winner.
    """
    edits = list(edits)
    if not edits:
        raise ValueError("resolve_section requires at least one edit")

    sections = {e.section for e in edits}
    if len(sections) > 1:
        raise ValueError(f"All edits must target one section, got: {sorted(sections)}")

    # Two-stage stable sort. First by id ascending, so an exact
    # (authority, timestamp) tie resolves to the smallest id. Then by
    # (authority, timestamp) descending — Python's sort is stable, so the id
    # ordering survives within equal keys.
    ranked = sorted(edits, key=lambda e: e.edit_id)
    ranked.sort(key=lambda e: (e.authority, e.when), reverse=True)
    winner, losers = ranked[0], ranked[1:]

    if not losers:
        return Resolution(section=winner.section, winner=winner, reason="uncontested")

    if winner.authority > losers[0].authority:
        reason = (
            f"{winner.author_role} entry takes precedence over "
            f"{losers[0].author_role} entry"
        )
    else:
        reason = f"most recent {winner.author_role} entry wins"

    return Resolution(
        section=winner.section,
        winner=winner,
        superseded=losers,
        conflict=True,
        reason=reason,
    )


def resolve_all(edits: Iterable[Edit]) -> dict[str, Resolution]:
    """
    Resolve a batch spanning several sections.

    Edits to DIFFERENT sections never contend — they are independent and all
    survive. That is the non-destructive merge property test_concurrent_edits
    demonstrates; only same-section edits enter the ordering above.
    """
    by_section: dict[str, list[Edit]] = {}
    for edit in edits:
        by_section.setdefault(edit.section, []).append(edit)
    return {section: resolve_section(group) for section, group in sorted(by_section.items())}


def conflict_metadata(resolution: Resolution) -> dict[str, Any]:
    """
    Metadata for a flagged conflict. Deliberately carries no clinical content —
    only ids, roles and a reason, so the audit trail stays PHI-free.
    """
    return {
        "conflict_flagged": resolution.conflict,
        "requires_review": resolution.requires_review,
        "resolution_reason": resolution.reason,
        "winning_author_role": resolution.winner.author_role,
        "winning_edit_id": resolution.winner.edit_id,
        "superseded_edit_ids": [e.edit_id for e in resolution.superseded],
        "superseded_author_roles": [e.author_role for e in resolution.superseded],
    }
