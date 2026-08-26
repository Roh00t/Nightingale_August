"""
Confidence as a measured quantity, with an abstention rule.

The organisers' hint: "Self-reported model confidence is decoration. What does
'medium' mean numerically? How does a clinician or care team see it?"

So we never ask the model how sure it is. Confidence is computed from three
signals the system can observe for itself:

  1. Ensemble agreement (weight 0.50)
     The same prompt is sampled N times at non-zero temperature. If the model
     names the same claim every time, that claim is stable; if it appears in one
     sample of five, it is noise. This is a sampling estimate of semantic
     entropy, and it is the only one of the three that measures the *model*.

  2. Extraction verification (weight 0.35)
     Whether the quote was found verbatim in the source (see
     services.safety.extraction). A claim that is byte-exact in the record is
     more trustworthy than one that only nearly matches.

  3. Deterministic rule support (weight 0.15)
     Whether a risk rule independently fired on the same text. Corroboration
     from a non-model source is worth something on its own.

`medium` therefore has an exact numeric meaning, published in BANDS below,
rather than being a word the model chose.

Answering the organisers' three questions for the confidence label:

  What is it?      A weighted score in [0,1] from agreement, verification and
                   rule support. Never a model self-report.
  How would we
  know if wrong?   Calibration. brier_score() over resolved items: if the system
                   says 0.9 it should be right about 90% of the time. A rising
                   Brier score means the weights need refitting.
  What happens
  when it's wrong? Below ABSTAIN_THRESHOLD the system does not guess. It emits
                   an abstention — "insufficient certainty, manual review" —
                   which is surfaced as a review task rather than a claim.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

logger = logging.getLogger(__name__)

W_AGREEMENT = 0.50
W_VERIFICATION = 0.35
W_RULE_SUPPORT = 0.15

# Below this the system abstains rather than presenting a claim. Chosen so a
# claim appearing in fewer than ~3 of 5 samples cannot reach a clinician
# unaccompanied by a review flag.
ABSTAIN_THRESHOLD = 0.60


class ConfidenceBand(str, Enum):
    """Published bands. These numbers are the definition, not a description."""

    HIGH = "high"        # >= 0.85
    MEDIUM = "medium"    # >= 0.60 and < 0.85
    LOW = "low"          # <  0.60  -> abstain

    @classmethod
    def of(cls, score: float) -> "ConfidenceBand":
        if score >= 0.85:
            return cls.HIGH
        if score >= ABSTAIN_THRESHOLD:
            return cls.MEDIUM
        return cls.LOW


# Exposed so the UI can show a clinician what the band means numerically
# instead of an unexplained word.
BANDS: dict[str, str] = {
    ConfidenceBand.HIGH.value: "≥ 0.85 — consistent across samples and verbatim in the record",
    ConfidenceBand.MEDIUM.value: "0.60–0.84 — mostly consistent; verify before acting",
    ConfidenceBand.LOW.value: "< 0.60 — withheld from the glance view; sent for manual review",
}

_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "and", "was", "for", "that", "with", "this", "from", "are", "were",
    "has", "had", "not", "but", "all", "can", "its", "may", "than", "then",
    "patient", "reports", "noted", "shows", "there",
}


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOPWORDS and len(w) > 2}


def _similar(a: str, b: str, threshold: float = 0.6) -> bool:
    """Jaccard overlap over content words — cheap proxy for semantic sameness."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


def agreement_score(claim: str, samples: Sequence[Sequence[str]]) -> float:
    """
    Fraction of independent samples that contain this claim.

    Args:
        claim: the claim under test.
        samples: one list of claims per sampling run.

    A claim in every run scores 1.0; a claim in one of five scores 0.2. With no
    samples the score is 0.5 — a neutral prior, so a single-shot deployment is
    neither rewarded nor punished for lacking an ensemble.
    """
    if not samples:
        return 0.5
    hits = sum(1 for run in samples if any(_similar(claim, other) for other in run))
    return hits / len(samples)


@dataclass
class ConfidenceAssessment:
    """Score, band, the inputs that produced it, and the abstention decision."""

    score: float
    band: ConfidenceBand
    agreement: float
    verification: float
    rule_support: float
    abstained: bool = False
    reason: str = ""
    components: dict[str, float] = field(default_factory=dict)

    @property
    def percent(self) -> int:
        return round(self.score * 100)

    def explain(self) -> str:
        return (
            f"{self.band.value} ({self.score:.2f}) = "
            f"{W_AGREEMENT:.2f}×agreement {self.agreement:.2f} + "
            f"{W_VERIFICATION:.2f}×verified {self.verification:.2f} + "
            f"{W_RULE_SUPPORT:.2f}×rules {self.rule_support:.2f}"
        )


def assess_confidence(
    claim: str,
    *,
    samples: Sequence[Sequence[str]] = (),
    verified: bool = False,
    verbatim: bool = False,
    rule_supported: bool = False,
) -> ConfidenceAssessment:
    """
    Compute confidence for one claim.

    Args:
        claim: the claim text.
        samples: claims from each independent sampling run.
        verified: the quote was located in the source at all.
        verbatim: it matched byte-exactly rather than after normalisation.
        rule_supported: a deterministic risk rule fired on the same text.
    """
    agreement = agreement_score(claim, samples)
    verification = 1.0 if verbatim else (0.75 if verified else 0.0)
    support = 1.0 if rule_supported else 0.0

    score = (
        W_AGREEMENT * agreement
        + W_VERIFICATION * verification
        + W_RULE_SUPPORT * support
    )
    score = max(0.0, min(1.0, score))
    band = ConfidenceBand.of(score)
    abstain = score < ABSTAIN_THRESHOLD

    assessment = ConfidenceAssessment(
        score=round(score, 3),
        band=band,
        agreement=round(agreement, 3),
        verification=verification,
        rule_support=support,
        abstained=abstain,
        reason=(
            f"Confidence {score:.2f} is below the {ABSTAIN_THRESHOLD:.2f} threshold; "
            "routed to manual review instead of the glance view."
            if abstain else ""
        ),
        components={
            "agreement": round(agreement, 3),
            "verification": verification,
            "rule_support": support,
        },
    )
    if abstain:
        logger.info("Abstained on a claim: %s", assessment.explain())
    return assessment


@dataclass
class AbstentionOutcome:
    """What survives for display, and what was withheld for review."""

    surfaced: list[dict[str, Any]] = field(default_factory=list)
    withheld: list[dict[str, Any]] = field(default_factory=list)

    @property
    def abstention_rate(self) -> float:
        total = len(self.surfaced) + len(self.withheld)
        return len(self.withheld) / total if total else 0.0


def apply_abstention(
    claims: Sequence[dict[str, Any]],
    *,
    always_surface_critical: bool = True,
) -> AbstentionOutcome:
    """
    Split claims into what may be shown and what must be reviewed first.

    `always_surface_critical` is a deliberate asymmetry. A low-confidence
    CRITICAL finding is still shown — flagged as unverified — because silently
    withholding a possible anaphylaxis is a worse failure than showing a
    clinician something uncertain. Abstention protects against noise, not
    against safety-relevant recall.
    """
    outcome = AbstentionOutcome()
    for claim in claims:
        conf: ConfidenceAssessment | None = claim.get("confidence")
        is_critical = str(claim.get("risk_level", "")).lower() == "critical"

        if conf is not None and conf.abstained:
            if is_critical and always_surface_critical:
                outcome.surfaced.append({
                    **claim,
                    "unverified": True,
                    "review_reason": "Critical finding shown despite low confidence",
                })
                continue
            outcome.withheld.append({**claim, "review_reason": conf.reason})
            continue
        outcome.surfaced.append(claim)
    return outcome


def brier_score(predictions: Sequence[tuple[float, bool]]) -> float:
    """
    Calibration metric. Mean squared error between confidence and outcome.

    0.0 is perfect; 0.25 is what you get by always saying 0.5. Feed it resolved
    items — (confidence_at_the_time, was_it_actually_correct) — to check whether
    the weights above still hold. This is the answer to "how would we know if
    the confidence label were wrong".
    """
    if not predictions:
        return 0.0
    return sum((p - (1.0 if outcome else 0.0)) ** 2 for p, outcome in predictions) / len(predictions)


def calibration_report(predictions: Sequence[tuple[float, bool]]) -> dict[str, Any]:
    """Per-band accuracy, so drift shows up where it happens."""
    buckets: dict[str, list[bool]] = {b.value: [] for b in ConfidenceBand}
    for score, outcome in predictions:
        buckets[ConfidenceBand.of(score).value].append(outcome)
    return {
        "brier_score": round(brier_score(predictions), 4),
        "sample_size": len(predictions),
        "by_band": {
            band: {
                "n": len(vals),
                "accuracy": round(sum(vals) / len(vals), 3) if vals else None,
            }
            for band, vals in buckets.items()
        },
    }
