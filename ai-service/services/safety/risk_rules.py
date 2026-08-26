"""
Deterministic risk floors. The model may raise risk; it may never lower it.

The organisers' hint: "Model-assigned ordinal labels inflate or drift between
runs. What deterministic rules can set a floor for the model?"

An LLM asked for "critical / high / medium / low" produces an ordinal that is
not stable across runs, prompt phrasings, or model versions. Nothing anchors it.
So the ordinal is not trusted as the answer — it is trusted only as a *proposal*,
and the final level is:

    final = max(deterministic_floor, model_proposal)

The floor is computed by regex and numeric comparison over the source text. It
is reproducible, inspectable, diffable in review, and unchanged by a model
upgrade. If the model says "low" on text containing "anaphylaxis", the floor
wins and the item is CRITICAL.

Answering the organisers' three questions for the risk badge:

  What is it?      The higher of a deterministic rule outcome and a model
                   proposal, always carrying the rule that produced the floor.
  How would we
  know if wrong?   Each floor names its trigger, so a wrong badge is traced to a
                   specific rule and pattern rather than to model temperament.
                   Rules are unit tested against the phrasings they must catch.
  What happens
  when it's wrong? A floor that fires wrongly over-escalates — the safe
                   direction. A floor that misses degrades to the model's
                   proposal, which is why the critical patterns are broad and
                   the tests enumerate real phrasings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum


class RiskLevel(IntEnum):
    """Ordered so comparison and max() are meaningful."""

    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @classmethod
    def parse(cls, value: object) -> "RiskLevel":
        """Coerce a model-supplied label. Unrecognised input becomes INFO."""
        if isinstance(value, cls):
            return value
        name = str(value or "").strip().upper()
        return cls.__members__.get(name, cls.INFO)

    @property
    def label(self) -> str:
        return self.name.lower()


@dataclass(frozen=True)
class RiskRule:
    """One deterministic rule: a pattern, the floor it sets, and why."""

    name: str
    pattern: re.Pattern[str]
    floor: RiskLevel
    rationale: str


def _p(regex: str) -> re.Pattern[str]:
    return re.compile(regex, re.IGNORECASE)


# Immediate-danger language. Broad on purpose: a false escalation costs a
# clinician a glance, a miss costs more.
CRITICAL_RULES: list[RiskRule] = [
    RiskRule("anaphylaxis", _p(r"\banaphyla(?:xis|ctic)\b"), RiskLevel.CRITICAL,
             "Anaphylaxis is an immediate life threat"),
    RiskRule("code_blue", _p(r"\bcode\s+blue\b|\bcardiac\s+arrest\b|\bresuscitat"), RiskLevel.CRITICAL,
             "Resuscitation event"),
    RiskRule("overdose", _p(r"\boverdose\b|\btoxicity\b|\bpoisoning\b"), RiskLevel.CRITICAL,
             "Medication overdose or toxicity"),
    RiskRule("self_harm", _p(r"\bsuicidal?\b|\bself[-\s]?harm\b|\bideation\b"), RiskLevel.CRITICAL,
             "Self-harm risk requires immediate escalation"),
    RiskRule("sepsis", _p(r"\bsepsis\b|\bseptic\b|\bsystemic\s+infection\b"), RiskLevel.CRITICAL,
             "Sepsis is time-critical"),
    RiskRule("stroke_mi", _p(r"\bstroke\b|\bmyocardial\s+infarction\b|\bSTEMI\b|\bpulmonary\s+embolism\b"),
             RiskLevel.CRITICAL, "Acute vascular event"),
    RiskRule("airway", _p(r"\brespiratory\s+(?:arrest|failure)\b|\bairway\s+compromise\b|\bstridor\b"),
             RiskLevel.CRITICAL, "Airway or respiratory failure"),
    RiskRule("haemorrhage", _p(r"\bhaemorrhag|\bhemorrhag|\buncontrolled\s+bleeding\b"),
             RiskLevel.CRITICAL, "Active haemorrhage"),
]

HIGH_RULES: list[RiskRule] = [
    RiskRule("hyperkalemia", _p(r"\bhyperkal(?:a|e)emia\b"), RiskLevel.HIGH,
             "Elevated potassium risks arrhythmia"),
    RiskRule("chest_pain", _p(r"\bchest\s+pain\b|\bangina\b"), RiskLevel.HIGH,
             "Chest pain requires cardiac exclusion"),
    RiskRule("dyspnea", _p(r"\bdyspn(?:o)?ea\b|\bshortness\s+of\s+breath\b|\bSOB\b"), RiskLevel.HIGH,
             "New breathlessness may indicate decompensation"),
    RiskRule("fall", _p(r"\bfall\b|\bfell\b|\bunwitnessed\s+fall\b"), RiskLevel.HIGH,
             "Falls carry injury and deterioration risk"),
    RiskRule("declining_renal", _p(r"\beGFR\b.{0,40}\b(?:drop|declin|fell|fall)"), RiskLevel.HIGH,
             "Declining renal function"),
    RiskRule("overdue_referral", _p(r"\breferral\b.{0,40}\b(?:pending|overdue|waitlist|not\s+scheduled)\b"),
             RiskLevel.HIGH, "Unresolved referral is an open safety gap"),
    RiskRule("non_adherence", _p(r"\b(?:non[-\s]?adheren|not\s+taking|stopped\s+taking|missed\s+doses)\b"),
             RiskLevel.HIGH, "Medication non-adherence"),
]

# Numeric thresholds. A number does not drift the way an adjective does.
# (label, regex capturing the value, comparator, bound, floor, rationale)
VITAL_RULES: list[tuple[str, re.Pattern[str], str, float, RiskLevel, str]] = [
    ("potassium", _p(r"\bpotassium\s*:?\s*([0-9]+(?:\.[0-9]+)?)"), ">=", 6.0,
     RiskLevel.CRITICAL, "Potassium >= 6.0 mmol/L risks arrhythmia"),
    ("potassium_low", _p(r"\bpotassium\s*:?\s*([0-9]+(?:\.[0-9]+)?)"), "<=", 2.9,
     RiskLevel.CRITICAL, "Potassium <= 2.9 mmol/L risks arrhythmia"),
    ("potassium_high", _p(r"\bpotassium\s*:?\s*([0-9]+(?:\.[0-9]+)?)"), ">=", 5.0,
     RiskLevel.HIGH, "Potassium >= 5.0 mmol/L is above range"),
    ("egfr", _p(r"\beGFR\s*(?:of|:|=|dropped\s+to|at)?\s*([0-9]+(?:\.[0-9]+)?)"), "<=", 30.0,
     RiskLevel.CRITICAL, "eGFR <= 30 indicates severe renal impairment"),
    ("egfr_moderate", _p(r"\beGFR\s*(?:of|:|=|dropped\s+to|at)?\s*([0-9]+(?:\.[0-9]+)?)"), "<=", 45.0,
     RiskLevel.HIGH, "eGFR <= 45 indicates stage 3b chronic kidney disease"),
    ("systolic_high", _p(r"\bBP\s*:?\s*([0-9]{2,3})\s*/"), ">=", 180.0,
     RiskLevel.CRITICAL, "Systolic >= 180 mmHg is a hypertensive crisis"),
    ("systolic_low", _p(r"\bBP\s*:?\s*([0-9]{2,3})\s*/"), "<=", 90.0,
     RiskLevel.CRITICAL, "Systolic <= 90 mmHg indicates hypotension"),
    ("spo2", _p(r"\bSpO2\s*:?\s*([0-9]{1,3})\s*%?"), "<=", 92.0,
     RiskLevel.HIGH, "SpO2 <= 92% indicates hypoxaemia"),
    ("hba1c", _p(r"\b(?:A1C|HbA1c)\s*:?\s*([0-9]+(?:\.[0-9]+)?)"), ">=", 9.0,
     RiskLevel.HIGH, "HbA1c >= 9% indicates poor glycaemic control"),
]

# Negation and hypothetical guards. "denies chest pain" and "no evidence of
# sepsis" must not escalate — over-escalation on negated findings is precisely
# the alert-fatigue driver the organisers warned about.
# Negation cues that PRECEDE the finding: "denies chest pain".
_NEGATION_BEFORE = _p(
    r"\b(?:no|not|denies|denied|negative\s+for|without|absence\s+of|"
    r"free\s+of|nil|never)\b"
)

# Cues that FOLLOW it: "anaphylaxis ruled out", "sepsis excluded".
# Clinical notes negate in both directions and a backward-only check misses half
# of them, escalating on findings that were explicitly excluded — one of the
# clearest alert-fatigue drivers there is.
_NEGATION_AFTER = _p(
    r"^\W{0,3}(?:was\s+|is\s+|has\s+been\s+)?(?:ruled\s+out|excluded|"
    r"not\s+present|resolved|negative|unlikely|improbable)\b"
)

_NEGATION_WINDOW = 45
# Tighter forward window: a cue further away probably belongs to a different
# clause. Erring toward escalation here is the safe direction.
_NEGATION_AFTER_WINDOW = 30


@dataclass
class RiskAssessment:
    """The final level plus every rule that contributed to the floor."""

    level: RiskLevel
    floor: RiskLevel
    model_proposal: RiskLevel
    triggered: list[RiskRule] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.level.label

    @property
    def floor_applied(self) -> bool:
        """True when the deterministic floor overrode a lower model proposal."""
        return self.floor > self.model_proposal

    @property
    def reasons(self) -> list[str]:
        return [r.rationale for r in self.triggered]

    def explain(self) -> str:
        if not self.triggered:
            return f"No deterministic rule fired; model proposal '{self.model_proposal.label}' stands."
        rules = ", ".join(r.name for r in self.triggered)
        if self.floor_applied:
            return (
                f"Raised from '{self.model_proposal.label}' to '{self.level.label}' "
                f"by rule(s): {rules}"
            )
        return f"Model proposal '{self.level.label}' met or exceeded floor; rule(s): {rules}"


def _is_negated(text: str, match_start: int, match_end: int) -> bool:
    """True if a negation cue brackets the match on either side."""
    before = text[max(0, match_start - _NEGATION_WINDOW) : match_start]
    if _NEGATION_BEFORE.search(before):
        return True
    after = text[match_end : match_end + _NEGATION_AFTER_WINDOW]
    return bool(_NEGATION_AFTER.search(after))


def _compare(value: float, op: str, bound: float) -> bool:
    return value >= bound if op == ">=" else value <= bound


def deterministic_floor(text: str) -> tuple[RiskLevel, list[RiskRule]]:
    """
    Compute the risk floor for a piece of clinical text.

    Returns the floor and every rule that fired, so the badge can always say why.
    """
    if not text:
        return RiskLevel.INFO, []

    triggered: list[RiskRule] = []

    for rule in (*CRITICAL_RULES, *HIGH_RULES):
        match = rule.pattern.search(text)
        if match and not _is_negated(text, match.start(), match.end()):
            triggered.append(rule)

    for name, pattern, op, bound, floor, rationale in VITAL_RULES:
        match = pattern.search(text)
        if not match:
            continue
        try:
            value = float(match.group(1))
        except (TypeError, ValueError):
            continue
        if _compare(value, op, bound):
            triggered.append(
                RiskRule(name, pattern, floor, f"{rationale} (observed {match.group(1)})")
            )

    floor = max((r.floor for r in triggered), default=RiskLevel.INFO)
    return floor, triggered


def assess_risk(text: str, model_proposal: object = "info") -> RiskAssessment:
    """
    Combine the deterministic floor with the model's proposal.

    The model can only ever raise the level. This is the whole guarantee: a
    prompt change, a temperature change, or a model swap cannot lower the risk
    of text containing 'anaphylaxis'.
    """
    proposal = RiskLevel.parse(model_proposal)
    floor, triggered = deterministic_floor(text)
    return RiskAssessment(
        level=max(floor, proposal),
        floor=floor,
        model_proposal=proposal,
        triggered=triggered,
    )


def is_critical_class(assessment: RiskAssessment) -> bool:
    """
    Items that must not be dismissible on autopilot.

    Used by the feedback loop to stop a stressed care team swiping away the
    class of alert that matters most.
    """
    return assessment.level >= RiskLevel.CRITICAL or any(
        r.floor >= RiskLevel.CRITICAL for r in assessment.triggered
    )
