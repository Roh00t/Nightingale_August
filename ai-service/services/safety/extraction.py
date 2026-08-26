"""
Verbatim extraction with provenance validation.

The organisers' hint: "Extraction or generation. Decide before you write a
prompt. Paraphrasing does not retain an origin, so what gets validated?"

We chose EXTRACTION. Every clinical claim the AI surfaces must be an exact
substring of a source entry, not a paraphrase of one. That choice buys a
property generation cannot offer: the claim is verifiable by string search, so a
hallucinated claim is *detectable in code* rather than only by a clinician
noticing it reads wrong.

The validator answers the three questions the organisers said they would ask of
every number on screen:

  What is it?      A byte-exact span in a named source entry.
  How would we
  know if wrong?   The quote either occurs in the source text or it does not.
                   No judgement, no model, no threshold.
  What happens
  when it's wrong? The claim is REJECTED and never stored. It cannot reach a
                   clinician, so a hallucination degrades recall, never
                   correctness.

Normalisation is deliberately conservative. Whitespace and quote-character
differences are tolerated because they are artefacts of tokenisation. Anything
that changes words — including dropping a negation — fails.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

logger = logging.getLogger(__name__)


class ExtractionVerdict(str, Enum):
    """Outcome of validating one claimed quote against its source."""

    EXACT = "exact"                 # byte-identical
    NORMALIZED = "normalized"       # differs only in whitespace/punctuation form
    REJECTED_NOT_FOUND = "rejected_not_found"        # not in the source: hallucinated
    REJECTED_NO_SOURCE = "rejected_no_source"        # source entry does not exist
    REJECTED_EMPTY = "rejected_empty"                # nothing to verify

    @property
    def accepted(self) -> bool:
        return self in (ExtractionVerdict.EXACT, ExtractionVerdict.NORMALIZED)


@dataclass
class VerifiedClaim:
    """A claim that survived validation, anchored to a character span."""

    quote: str
    source_entry_id: str
    span_from: int
    span_to: int
    verdict: ExtractionVerdict
    category: str = "observation"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.verdict.accepted


# Characters that models routinely substitute. Normalising these is safe because
# none of them changes a word.
_QUOTE_MAP = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
}


def _normalize(text: str) -> str:
    """Fold typographic variation and collapse whitespace. Words are untouched."""
    text = unicodedata.normalize("NFKC", text)
    for src, dst in _QUOTE_MAP.items():
        text = text.replace(src, dst)
    return re.sub(r"\s+", " ", text).strip()


def _find_normalized(needle: str, haystack: str) -> tuple[int, int] | None:
    """
    Locate `needle` in `haystack` ignoring whitespace/typographic form, and map
    the hit back to offsets in the ORIGINAL haystack.

    Offsets must address the stored text, because that is what a clinician sees
    when they click through to the source.
    """
    norm_needle = _normalize(needle)
    if not norm_needle:
        return None

    # Walk the original string building a normalised projection alongside an
    # index map, so a match in normalised space yields real offsets.
    projected: list[str] = []
    index_map: list[int] = []
    prev_space = True
    for i, ch in enumerate(unicodedata.normalize("NFKC", haystack)):
        ch = _QUOTE_MAP.get(ch, ch)
        if ch.isspace():
            if prev_space:
                continue
            projected.append(" ")
            index_map.append(i)
            prev_space = True
        else:
            projected.append(ch)
            index_map.append(i)
            prev_space = False

    projection = "".join(projected).strip()
    # Account for characters stripped from the front.
    offset = len("".join(projected)) - len("".join(projected).lstrip())
    pos = projection.find(norm_needle)
    if pos < 0:
        return None

    start_idx = pos + offset
    end_idx = start_idx + len(norm_needle) - 1
    if start_idx >= len(index_map) or end_idx >= len(index_map):
        return None
    return index_map[start_idx], index_map[end_idx] + 1


def verify_quote(quote: str, source_text: str) -> tuple[ExtractionVerdict, int, int]:
    """
    Check that `quote` genuinely occurs in `source_text`.

    Returns the verdict and the span. A rejected claim gets span (0, 0) — never
    a guessed offset, because a fabricated span is worse than no span: it points
    a clinician at text that does not support the claim.
    """
    if not quote or not quote.strip() or not source_text:
        return ExtractionVerdict.REJECTED_EMPTY, 0, 0

    idx = source_text.find(quote)
    if idx >= 0:
        return ExtractionVerdict.EXACT, idx, idx + len(quote)

    found = _find_normalized(quote, source_text)
    if found:
        return ExtractionVerdict.NORMALIZED, found[0], found[1]

    return ExtractionVerdict.REJECTED_NOT_FOUND, 0, 0


def verify_claims(
    claims: Iterable[dict[str, Any]],
    sources: dict[str, str],
) -> tuple[list[VerifiedClaim], list[dict[str, Any]]]:
    """
    Validate a batch of model-proposed claims against their source entries.

    Args:
        claims: dicts with at least `quote` and `source_entry_id`.
        sources: entry id -> that entry's stored text.

    Returns:
        (accepted, rejected). Rejected entries carry a `rejection_reason` so the
        failure is auditable rather than silent — a model that starts
        paraphrasing should show up as a rising rejection rate, not as quietly
        vanishing highlights.
    """
    accepted: list[VerifiedClaim] = []
    rejected: list[dict[str, Any]] = []

    for claim in claims:
        quote = str(claim.get("quote", "") or "")
        entry_id = str(claim.get("source_entry_id", "") or "")
        source = sources.get(entry_id)

        if source is None:
            rejected.append({**claim, "rejection_reason": ExtractionVerdict.REJECTED_NO_SOURCE.value})
            logger.warning("Claim references unknown source entry %s", entry_id or "<missing>")
            continue

        verdict, start, end = verify_quote(quote, source)
        if not verdict.accepted:
            rejected.append({**claim, "rejection_reason": verdict.value})
            # Log the length, never the text: a rejected quote may still contain PHI.
            logger.warning(
                "Rejected unverifiable claim on entry %s (%d chars, %s)",
                entry_id, len(quote), verdict.value,
            )
            continue

        accepted.append(
            VerifiedClaim(
                quote=source[start:end],   # store the SOURCE form, not the model's
                source_entry_id=entry_id,
                span_from=start,
                span_to=end,
                verdict=verdict,
                category=str(claim.get("category", "observation")),
                metadata={k: v for k, v in claim.items()
                          if k not in {"quote", "source_entry_id", "category"}},
            )
        )

    if rejected:
        logger.info(
            "Extraction validation: %d accepted, %d rejected as unverifiable",
            len(accepted), len(rejected),
        )
    return accepted, rejected


def extraction_rejection_rate(accepted: int, rejected: int) -> float:
    """
    Share of claims that failed validation.

    Worth monitoring: a rate that climbs means the model has drifted toward
    paraphrase, which is the failure this whole module exists to catch.
    """
    total = accepted + rejected
    return (rejected / total) if total else 0.0
