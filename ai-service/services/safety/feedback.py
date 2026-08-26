"""
Feedback-loop safety: exposure bias, alert fatigue, and critical floors.

The organisers' hint: "A few real world hazards here. Exposure bias since only
surfaced items get feedback. Care team fatigue is real, so they could dismiss
items under stress load so critical classes need a floor."

Both hazards are structural, not bugs, and neither is fixed by better prompting.

EXPOSURE BIAS
  The loop only sees items it surfaced. If something is scored low and hidden,
  nobody corrects it, so the system never learns it was wrong — the error is
  self-reinforcing and invisible in the metrics, because precision on surfaced
  items looks fine while recall quietly rots.

  Mitigation: audit_sample() deliberately promotes a random slice of LOW-scored,
  unsurfaced items into a review queue. Reviewing them is the only way to
  measure false negatives. The sample is random, not "most nearly surfaced",
  because sampling near the threshold measures the boundary rather than the
  blind spot.

ALERT FATIGUE
  A care team under load clicks dismiss. Learning from those dismissals teaches
  the system to hide exactly what it should show, and it compounds: hidden items
  get no feedback, which is exposure bias again.

  Mitigations:
    - Critical classes have a floor no amount of learned weight can push below.
    - Critical items cannot be bulk-dismissed, and require a typed reason.
    - Dismissals made during a fatigue burst are recorded but excluded from
      training, so a stressed shift does not permanently degrade the model.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any, Sequence

from services.safety.risk_rules import RiskLevel

logger = logging.getLogger(__name__)

# A critical item never scores below this, whatever the learned weight says.
CRITICAL_IMPORTANCE_FLOOR = 0.90
HIGH_IMPORTANCE_FLOOR = 0.70

# Share of low-scored items promoted for audit, to measure false negatives.
AUDIT_SAMPLE_RATE = 0.05

# More than this many dismissals inside the window is treated as a fatigue
# burst: still honoured in the UI, but not fed to training.
FATIGUE_DISMISSAL_THRESHOLD = 10
FATIGUE_WINDOW_MINUTES = 5


def apply_importance_floor(score: float, risk: RiskLevel) -> tuple[float, bool]:
    """
    Enforce the minimum importance for a risk class.

    Returns (score, floor_applied). The learned weight can raise an item's
    importance but can never bury a critical one — which is what stops a
    fatigued team's dismissals from training away the alerts that matter.
    """
    if risk >= RiskLevel.CRITICAL and score < CRITICAL_IMPORTANCE_FLOOR:
        return CRITICAL_IMPORTANCE_FLOOR, True
    if risk >= RiskLevel.HIGH and score < HIGH_IMPORTANCE_FLOOR:
        return HIGH_IMPORTANCE_FLOOR, True
    return score, False


@dataclass
class DismissalPolicy:
    """Whether an item may be dismissed, and on what terms."""

    allowed: bool
    requires_reason: bool
    allows_bulk: bool
    message: str = ""


def dismissal_policy(risk: RiskLevel) -> DismissalPolicy:
    """
    Friction proportional to severity.

    Low-risk noise is one click, because making it hard is itself a fatigue
    driver. Critical items need a typed reason and cannot be swept up in a bulk
    action, so dismissing one is always a deliberate act.
    """
    if risk >= RiskLevel.CRITICAL:
        return DismissalPolicy(
            allowed=True,
            requires_reason=True,
            allows_bulk=False,
            message="Critical findings require a typed reason and cannot be dismissed in bulk.",
        )
    if risk >= RiskLevel.HIGH:
        return DismissalPolicy(
            allowed=True,
            requires_reason=True,
            allows_bulk=False,
            message="High-risk findings require a reason for dismissal.",
        )
    return DismissalPolicy(allowed=True, requires_reason=False, allows_bulk=True)


def validate_dismissal(
    risk: RiskLevel, *, reason: str | None = None, bulk: bool = False
) -> tuple[bool, str]:
    """Check a dismissal against policy. Returns (accepted, explanation)."""
    policy = dismissal_policy(risk)

    if bulk and not policy.allows_bulk:
        return False, f"{risk.label} findings cannot be dismissed in bulk. Dismiss individually."

    if policy.requires_reason and not (reason or "").strip():
        return False, f"{risk.label} findings require a reason for dismissal."

    if policy.requires_reason and len((reason or "").strip()) < 8:
        return False, "Give a specific reason (at least 8 characters)."

    return True, "Dismissal recorded."


@dataclass
class FatigueSignal:
    """Whether recent dismissals look like fatigue rather than judgement."""

    dismissals_in_window: int
    fatigued: bool
    exclude_from_training: bool
    note: str = ""


def detect_fatigue(recent_dismissal_count: int) -> FatigueSignal:
    """
    Flag a dismissal burst.

    The dismissal still takes effect — overriding a clinician's action would be
    worse. But it is excluded from training data, so a bad shift does not teach
    the system to hide things permanently.
    """
    fatigued = recent_dismissal_count > FATIGUE_DISMISSAL_THRESHOLD
    return FatigueSignal(
        dismissals_in_window=recent_dismissal_count,
        fatigued=fatigued,
        exclude_from_training=fatigued,
        note=(
            f"{recent_dismissal_count} dismissals in {FATIGUE_WINDOW_MINUTES} minutes "
            "looks like fatigue; honoured in the UI but excluded from training."
            if fatigued else ""
        ),
    )


@dataclass
class AuditSample:
    """Unsurfaced items promoted for human review to measure false negatives."""

    sampled: list[dict[str, Any]] = field(default_factory=list)
    population_size: int = 0
    rate: float = AUDIT_SAMPLE_RATE

    @property
    def coverage(self) -> float:
        return len(self.sampled) / self.population_size if self.population_size else 0.0


def audit_sample(
    unsurfaced: Sequence[dict[str, Any]],
    *,
    rate: float = AUDIT_SAMPLE_RATE,
    rng: random.Random | None = None,
) -> AuditSample:
    """
    Randomly promote unsurfaced items into the review queue.

    This is the counterweight to exposure bias. Without it the only feedback the
    system receives is about items it already chose to show, so a false negative
    can never be discovered — the system would look accurate precisely because
    it is not being asked about what it missed.

    `rng` is injectable so the sampling is reproducible under test.
    """
    if not unsurfaced:
        return AuditSample(population_size=0, rate=rate)

    rng = rng or random.Random()
    # At least one item whenever anything was hidden: a 5% rate over a small
    # queue would otherwise round to zero and silently disable the audit.
    count = max(1, round(len(unsurfaced) * rate))
    sampled = rng.sample(list(unsurfaced), min(count, len(unsurfaced)))

    logger.info(
        "Audit sample: %d of %d unsurfaced items promoted for false-negative review",
        len(sampled), len(unsurfaced),
    )
    return AuditSample(
        sampled=[{**item, "audit_reason": "random false-negative audit"} for item in sampled],
        population_size=len(unsurfaced),
        rate=rate,
    )


def training_eligible(interaction: dict[str, Any]) -> bool:
    """
    Whether an interaction may inform learned weights.

    Excluded: dismissals during a fatigue burst, and any interaction with a
    critical item — critical classes are governed by deterministic floors, so
    letting them drift with usage would defeat the floor entirely.
    """
    if interaction.get("exclude_from_training"):
        return False
    if str(interaction.get("risk_level", "")).lower() == "critical":
        return False
    return True
