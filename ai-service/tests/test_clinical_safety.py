"""
Clinical safety layer — the guardrails that sit between the LLM and the record.

Every test here maps to a hazard the organisers named. The framing they gave for
any number on screen is: what is it, how would we know if it were wrong, and what
happens when it is. These tests are the "how would we know" half.

Pure unit tests: no database, no network, no model. They run anywhere.
"""

from __future__ import annotations

import random

import pytest

from services.safety.clinical_conflict import ConflictClass, detect_conflicts
from services.safety.confidence import (
    ABSTAIN_THRESHOLD,
    BANDS,
    ConfidenceBand,
    apply_abstention,
    assess_confidence,
    brier_score,
    calibration_report,
)
from services.safety.extraction import (
    ExtractionVerdict,
    extraction_rejection_rate,
    verify_claims,
    verify_quote,
)
from services.safety.feedback import (
    CRITICAL_IMPORTANCE_FLOOR,
    apply_importance_floor,
    audit_sample,
    detect_fatigue,
    dismissal_policy,
    training_eligible,
    validate_dismissal,
)
from services.safety.patient_gate import (
    GateVerdict,
    check_grounding,
    finalize_patient_message,
    screen_patient_draft,
)
from services.safety.risk_rules import RiskLevel, assess_risk, deterministic_floor

SOURCE = (
    "Lab results review: eGFR dropped to 45 (from 58 in June). Creatinine 1.4. "
    "Potassium 5.1 (borderline high). Increased Lisinopril to 10mg. "
    "Patient is not allergic to penicillin."
)


# ===========================================================================
# 1. Extraction over generation
# ===========================================================================

class TestExtractionProvenance:
    """A claim must be a verbatim span of a source entry, or it is rejected."""

    async def test_exact_quote_accepted(self):
        verdict, start, end = verify_quote("eGFR dropped to 45", SOURCE)
        assert verdict is ExtractionVerdict.EXACT
        assert SOURCE[start:end] == "eGFR dropped to 45"

    async def test_whitespace_variation_accepted_and_span_maps_to_source(self):
        """Tokenisation artefacts are tolerated; the span still addresses the real text."""
        verdict, start, end = verify_quote("eGFR   dropped  to 45", SOURCE)
        assert verdict is ExtractionVerdict.NORMALIZED
        assert SOURCE[start:end] == "eGFR dropped to 45"

    async def test_paraphrase_is_rejected(self):
        """
        The core decision. A paraphrase retains no origin, so there is nothing to
        validate against — it is rejected rather than stored unverifiable.
        """
        verdict, _, _ = verify_quote("kidney function has declined significantly", SOURCE)
        assert verdict is ExtractionVerdict.REJECTED_NOT_FOUND

    async def test_dropping_a_negation_is_rejected(self):
        """
        The catastrophic case: 'not allergic' becoming 'allergic'. Normalisation
        folds whitespace and quote characters only, never words.
        """
        verdict, _, _ = verify_quote("Patient is allergic to penicillin", SOURCE)
        assert verdict is ExtractionVerdict.REJECTED_NOT_FOUND

    async def test_rejected_claim_gets_no_fabricated_span(self):
        """A wrong span points a clinician at text that does not support the claim."""
        _, start, end = verify_quote("invented finding", SOURCE)
        assert (start, end) == (0, 0)

    async def test_batch_separates_accepted_from_rejected_with_reasons(self):
        accepted, rejected = verify_claims(
            [
                {"quote": "Potassium 5.1", "source_entry_id": "e1"},
                {"quote": "patient is deteriorating rapidly", "source_entry_id": "e1"},
                {"quote": "anything", "source_entry_id": "missing-entry"},
            ],
            {"e1": SOURCE},
        )
        assert len(accepted) == 1
        assert {r["rejection_reason"] for r in rejected} == {
            ExtractionVerdict.REJECTED_NOT_FOUND.value,
            ExtractionVerdict.REJECTED_NO_SOURCE.value,
        }

    async def test_accepted_claim_stores_source_form_not_model_form(self):
        """What is stored is the record's wording, so display cannot drift."""
        accepted, _ = verify_claims(
            [{"quote": "eGFR    dropped   to 45", "source_entry_id": "e1"}], {"e1": SOURCE}
        )
        assert accepted[0].quote == "eGFR dropped to 45"

    async def test_rejection_rate_is_observable(self):
        """A rising rate means the model drifted toward paraphrase."""
        assert extraction_rejection_rate(8, 2) == pytest.approx(0.2)
        assert extraction_rejection_rate(0, 0) == 0.0


# ===========================================================================
# 2. Deterministic risk floors
# ===========================================================================

class TestRiskFloors:
    """The model may raise risk. It may never lower it."""

    @pytest.mark.parametrize("text,rule", [
        ("Patient developed anaphylaxis after the dose", "anaphylaxis"),
        ("Code blue called at 03:00", "code_blue"),
        ("Suspected paracetamol overdose", "overdose"),
        ("Expressed suicidal ideation", "self_harm"),
        ("Presentation consistent with sepsis", "sepsis"),
    ])
    async def test_critical_language_forces_critical_regardless_of_model(self, text, rule):
        assessment = assess_risk(text, model_proposal="low")
        assert assessment.level is RiskLevel.CRITICAL
        assert assessment.floor_applied
        assert rule in [r.name for r in assessment.triggered]

    async def test_model_cannot_lower_below_the_floor(self):
        """Ordinal drift between runs cannot bury a critical finding."""
        for proposal in ("info", "low", "medium", "high", "critical"):
            assert assess_risk("anaphylaxis observed", proposal).level is RiskLevel.CRITICAL

    async def test_model_may_raise_above_the_floor(self):
        """The floor is a minimum, not an override — clinical nuance still counts."""
        assessment = assess_risk("Routine medication review", model_proposal="high")
        assert assessment.level is RiskLevel.HIGH
        assert assessment.floor is RiskLevel.INFO

    async def test_numeric_thresholds_fire_deterministically(self):
        """A number does not drift the way an adjective does."""
        assert assess_risk("Potassium 6.4 mmol/L").level is RiskLevel.CRITICAL
        assert assess_risk("Potassium 5.2 mmol/L").level is RiskLevel.HIGH
        assert assess_risk("Potassium 4.1 mmol/L").level is RiskLevel.INFO

    async def test_negated_findings_do_not_escalate(self):
        """
        Over-escalating on 'denies chest pain' is a direct alert-fatigue driver.
        """
        assert assess_risk("Patient denies chest pain").level is RiskLevel.INFO
        assert assess_risk("No evidence of sepsis").level is RiskLevel.INFO
        assert assess_risk("Anaphylaxis ruled out").level is RiskLevel.INFO

    async def test_every_floor_names_its_trigger(self):
        """A wrong badge must be traceable to a rule, not to model temperament."""
        assessment = assess_risk("Patient developed anaphylaxis", "info")
        assert assessment.triggered
        assert all(r.rationale for r in assessment.triggered)
        assert "anaphylaxis" in assessment.explain()

    async def test_unparseable_model_label_degrades_to_info_not_crash(self):
        assert assess_risk("Routine review", model_proposal="EXTREMELY URGENT!!!").level is RiskLevel.INFO

    async def test_assessment_is_reproducible(self):
        """Same input, same answer — every time."""
        results = {assess_risk(SOURCE, "low").level for _ in range(20)}
        assert len(results) == 1


# ===========================================================================
# 3. Confidence and abstention
# ===========================================================================

class TestConfidenceAndAbstention:
    """'Medium' has a number behind it, and low confidence abstains."""

    async def test_bands_are_numerically_defined(self):
        assert ConfidenceBand.of(0.90) is ConfidenceBand.HIGH
        assert ConfidenceBand.of(0.70) is ConfidenceBand.MEDIUM
        assert ConfidenceBand.of(0.40) is ConfidenceBand.LOW
        # The definition is published for the UI, not left implicit.
        assert set(BANDS) == {"high", "medium", "low"}

    async def test_stable_verified_claim_scores_high(self):
        runs = [["eGFR dropped to 45"]] * 5
        assessment = assess_confidence(
            "eGFR dropped to 45", samples=runs, verified=True, verbatim=True, rule_supported=True
        )
        assert assessment.band is ConfidenceBand.HIGH
        assert not assessment.abstained

    async def test_unstable_claim_abstains(self):
        """A claim in one sample of five is noise, not a finding."""
        runs = [["a"], ["b"], ["c"], ["d"], ["eGFR dropped to 45"]]
        assessment = assess_confidence("eGFR dropped to 45", samples=runs, verified=False)
        assert assessment.score < ABSTAIN_THRESHOLD
        assert assessment.abstained
        assert "manual review" in assessment.reason

    async def test_confidence_is_never_model_self_reported(self):
        """
        The score is a function of observed signals only. Passing no ensemble
        yields the neutral prior rather than whatever a model claimed.
        """
        assessment = assess_confidence("some claim", samples=(), verified=False)
        assert assessment.agreement == 0.5

    async def test_score_is_explainable_as_its_components(self):
        assessment = assess_confidence("x", samples=[["x"]], verified=True, verbatim=True)
        assert "agreement" in assessment.explain()
        assert set(assessment.components) == {"agreement", "verification", "rule_support"}

    async def test_low_confidence_items_are_withheld(self):
        low = assess_confidence("noise", samples=[["a"], ["b"], ["c"]], verified=False)
        outcome = apply_abstention([{"quote": "noise", "risk_level": "low", "confidence": low}])
        assert outcome.withheld and not outcome.surfaced
        assert outcome.abstention_rate == 1.0

    async def test_low_confidence_critical_findings_are_still_surfaced(self):
        """
        Deliberate asymmetry: silently withholding a possible anaphylaxis is a
        worse failure than showing a clinician something uncertain.
        """
        low = assess_confidence("possible anaphylaxis", samples=[["a"], ["b"], ["c"]], verified=False)
        outcome = apply_abstention([{"quote": "possible anaphylaxis",
                                     "risk_level": "critical", "confidence": low}])
        assert outcome.surfaced and not outcome.withheld
        assert outcome.surfaced[0]["unverified"] is True

    async def test_brier_score_measures_calibration(self):
        """Perfectly calibrated is 0.0; always guessing 0.5 is 0.25."""
        assert brier_score([(1.0, True), (0.0, False)]) == 0.0
        assert brier_score([(0.5, True), (0.5, False)]) == 0.25

    async def test_calibration_report_breaks_down_by_band(self):
        report = calibration_report([(0.9, True), (0.9, False), (0.4, False)])
        assert report["sample_size"] == 3
        assert report["by_band"]["high"]["n"] == 2
        assert report["by_band"]["high"]["accuracy"] == 0.5


# ===========================================================================
# 4. Human-human clinical contradictions
# ===========================================================================

class TestClinicalConflictDetection:
    """Surface the delta with both quotes. Never arbitrate."""

    @staticmethod
    def _entries():
        return [
            {"id": "e1", "author_id": "c1", "author_role": "clinician", "created_at": "2026-01-01",
             "content_text": "Increased Lisinopril to 10mg daily. Patient is allergic to penicillin."},
            {"id": "e2", "author_id": "s1", "author_role": "staff", "created_at": "2026-01-02",
             "content_text": "Administered Lisinopril 100mg as charted. Chart says not allergic to penicillin."},
        ]

    async def test_dosage_contradiction_detected(self):
        """Dr A says 10mg, Nurse B says 100mg — the case the organisers named."""
        conflicts = detect_conflicts(self._entries())
        dosage = [c for c in conflicts if c.conflict_class is ConflictClass.DOSAGE]
        assert dosage, "10mg vs 100mg was not detected"
        assert set(dosage[0].distinct_values) == {"10mg", "100mg"}

    async def test_allergy_contradiction_is_critical(self):
        conflicts = detect_conflicts(self._entries())
        allergy = [c for c in conflicts if c.conflict_class is ConflictClass.ALLERGY]
        assert allergy and allergy[0].severity == "critical"

    async def test_allergy_conflicts_are_ranked_first(self):
        assert detect_conflicts(self._entries())[0].conflict_class is ConflictClass.ALLERGY

    async def test_both_quotes_are_preserved_for_the_reviewer(self):
        """A clinician resolving a dosing conflict must see both exact wordings."""
        conflict = detect_conflicts(self._entries())[0]
        payload = conflict.to_metadata()
        assert len(payload["claims"]) == 2
        assert all(c["quote"] for c in payload["claims"])
        assert {c["author_role"] for c in payload["claims"]} == {"clinician", "staff"}

    async def test_system_never_auto_resolves(self):
        """The system has no basis to decide which clinician is right."""
        for conflict in detect_conflicts(self._entries()):
            assert conflict.requires_human_resolution is True

    async def test_same_author_revision_is_not_a_conflict(self):
        """One author correcting themselves over time is not a disagreement."""
        entries = [
            {"id": "e1", "author_id": "c1", "author_role": "clinician", "created_at": "2026-01-01",
             "content_text": "Started Lisinopril 5mg."},
            {"id": "e2", "author_id": "c1", "author_role": "clinician", "created_at": "2026-02-01",
             "content_text": "Increased Lisinopril to 10mg."},
        ]
        assert detect_conflicts(entries) == []

    async def test_changing_vitals_are_not_conflicts(self):
        """A BP differing across visits is the timeline working, not a disagreement."""
        entries = [
            {"id": "e1", "author_id": "c1", "author_role": "clinician", "created_at": "2025-04-01",
             "content_text": "BP 145/90 today."},
            {"id": "e2", "author_id": "s1", "author_role": "staff", "created_at": "2026-01-01",
             "content_text": "BP 128/78 today."},
        ]
        assert detect_conflicts(entries) == []

    async def test_agreement_produces_no_conflict(self):
        entries = [
            {"id": "e1", "author_id": "c1", "author_role": "clinician", "created_at": "2026-01-01",
             "content_text": "Lisinopril 10mg daily."},
            {"id": "e2", "author_id": "s1", "author_role": "staff", "created_at": "2026-01-02",
             "content_text": "Confirmed Lisinopril 10mg daily."},
        ]
        assert detect_conflicts(entries) == []


# ===========================================================================
# 5. Patient-facing firewall
# ===========================================================================

class TestPatientFacingFirewall:
    """The one path where AI output cannot reach its audience unaccompanied."""

    SRC = ["Increased Lisinopril to 10mg daily. BP improved to 128/78. Next visit March 2026."]

    async def test_grounded_draft_passes(self):
        assert screen_patient_draft(
            "Please continue taking Lisinopril 10mg every morning. Your BP is 128/78.", self.SRC
        ).verdict is GateVerdict.PASSED

    async def test_hallucinated_dose_is_blocked(self):
        """'take 100mg' when the record says 10mg — the catastrophic case."""
        result = screen_patient_draft("Please increase Lisinopril to 100mg every morning.", self.SRC)
        assert result.verdict is GateVerdict.BLOCKED_UNGROUNDED
        assert "100mg" in result.ungrounded_terms

    async def test_wrong_unit_is_blocked(self):
        """10mg and 10ml are different tokens; the number alone is not enough."""
        result = screen_patient_draft("Take Lisinopril 10ml every morning.", self.SRC)
        assert result.verdict is GateVerdict.BLOCKED_UNGROUNDED
        assert "10ml" in result.ungrounded_terms

    async def test_substring_does_not_count_as_grounded(self):
        """'1' must not be considered grounded by '10mg' appearing in the source."""
        assert "1" in check_grounding("Take 1 tablet.", self.SRC)

    @pytest.mark.parametrize("draft", [
        "You have chronic kidney disease.",
        "Your life expectancy is limited.",
        "Please stop taking your medication.",
        "There is no need to worry about this.",
        "As an AI, I think this looks fine.",
    ])
    async def test_prohibited_speech_acts_are_blocked(self, draft):
        """Diagnosis, prognosis and treatment changes are clinician speech acts."""
        assert screen_patient_draft(draft, self.SRC).verdict is GateVerdict.BLOCKED_PROHIBITED

    async def test_message_cannot_send_without_human_approval(self):
        result = screen_patient_draft("Your BP is 128/78.", self.SRC)
        assert result.verdict is GateVerdict.PASSED
        assert result.sendable is False, "a passing draft must still require approval"

    async def test_only_clinicians_and_admins_may_approve(self):
        message, result = finalize_patient_message(
            "Your BP is 128/78.", self.SRC,
            approver_id="s1", approver_role="staff", approver_name="Nurse James",
        )
        assert message is None
        assert result.verdict is GateVerdict.BLOCKED_NO_APPROVAL

    async def test_approval_cannot_rescue_a_blocked_draft(self):
        """A human signing off on invented content is what the gates prevent."""
        message, result = finalize_patient_message(
            "Take Lisinopril 100mg daily.", self.SRC,
            approver_id="c1", approver_role="clinician", approver_name="Dr. Chen",
        )
        assert message is None
        assert result.verdict is GateVerdict.BLOCKED_UNGROUNDED

    async def test_sent_message_carries_visible_attribution(self):
        message, result = finalize_patient_message(
            "Please continue taking Lisinopril 10mg every morning.", self.SRC,
            approver_id="c1", approver_role="clinician", approver_name="Dr. Sarah Chen",
        )
        assert message is not None
        assert "Dr. Sarah Chen" in message
        assert "AI assistance" in message
        assert result.to_metadata()["human_approved"] is True

    async def test_audit_metadata_records_the_decision(self):
        result = screen_patient_draft("Take Lisinopril 100mg.", self.SRC)
        meta = result.to_metadata()
        assert meta["patient_gate_verdict"] == GateVerdict.BLOCKED_UNGROUNDED.value
        assert meta["human_approved"] is False


# ===========================================================================
# 6. Feedback loop hazards
# ===========================================================================

class TestFeedbackLoopSafety:
    """Exposure bias and alert fatigue are structural, not prompting problems."""

    async def test_critical_items_cannot_be_buried_by_learned_weight(self):
        score, applied = apply_importance_floor(0.05, RiskLevel.CRITICAL)
        assert score == CRITICAL_IMPORTANCE_FLOOR and applied

    async def test_floor_does_not_inflate_low_risk_items(self):
        score, applied = apply_importance_floor(0.10, RiskLevel.LOW)
        assert score == 0.10 and not applied

    async def test_critical_dismissal_requires_a_typed_reason(self):
        assert validate_dismissal(RiskLevel.CRITICAL, reason=None)[0] is False
        assert validate_dismissal(RiskLevel.CRITICAL, reason="ok")[0] is False
        assert validate_dismissal(RiskLevel.CRITICAL, reason="Actioned in person today")[0] is True

    async def test_critical_items_cannot_be_bulk_dismissed(self):
        """Stops a fatigued team swiping away the class that matters most."""
        assert not dismissal_policy(RiskLevel.CRITICAL).allows_bulk
        accepted, msg = validate_dismissal(RiskLevel.CRITICAL, reason="Reviewed already", bulk=True)
        assert accepted is False and "bulk" in msg

    async def test_low_risk_dismissal_stays_one_click(self):
        """Friction on noise is itself a fatigue driver."""
        policy = dismissal_policy(RiskLevel.LOW)
        assert policy.allows_bulk and not policy.requires_reason

    async def test_dismissal_burst_is_excluded_from_training(self):
        """A bad shift must not permanently teach the system to hide things."""
        assert detect_fatigue(3).exclude_from_training is False
        assert detect_fatigue(25).exclude_from_training is True

    async def test_audit_sample_promotes_unsurfaced_items(self):
        """The only way to measure false negatives is to look at what was hidden."""
        population = [{"id": i} for i in range(100)]
        sample = audit_sample(population, rng=random.Random(42))
        assert 1 <= len(sample.sampled) <= 10
        assert all(s["audit_reason"] for s in sample.sampled)

    async def test_audit_never_silently_disables_on_small_queues(self):
        """A 5% rate over 3 items would round to zero and disable the audit."""
        assert len(audit_sample([{"id": 1}, {"id": 2}, {"id": 3}], rng=random.Random(1)).sampled) >= 1

    async def test_audit_sampling_is_reproducible(self):
        population = [{"id": i} for i in range(50)]
        a = audit_sample(population, rng=random.Random(7)).sampled
        b = audit_sample(population, rng=random.Random(7)).sampled
        assert [x["id"] for x in a] == [x["id"] for x in b]

    async def test_critical_interactions_never_train_the_model(self):
        """Critical classes are governed by floors; letting them drift defeats the floor."""
        assert training_eligible({"risk_level": "critical"}) is False
        assert training_eligible({"risk_level": "high"}) is True
        assert training_eligible({"risk_level": "low", "exclude_from_training": True}) is False


# ===========================================================================
# 7. Layers composing
# ===========================================================================

class TestSafetyLayersCompose:
    """The guarantees must hold together, not just individually."""

    async def test_unverifiable_claim_never_reaches_a_clinician(self):
        """Rejected at extraction, so confidence and risk never even run."""
        accepted, rejected = verify_claims(
            [{"quote": "patient is dying", "source_entry_id": "e1"}], {"e1": SOURCE}
        )
        assert not accepted and len(rejected) == 1

    async def test_verified_critical_claim_survives_the_whole_pipeline(self):
        source = "Patient developed anaphylaxis following the first dose."
        accepted, _ = verify_claims(
            [{"quote": "developed anaphylaxis", "source_entry_id": "e1"}], {"e1": source}
        )
        assert accepted

        assessment = assess_risk(source, model_proposal="low")
        assert assessment.level is RiskLevel.CRITICAL

        confidence = assess_confidence(
            "developed anaphylaxis",
            samples=[["developed anaphylaxis"]] * 5,
            verified=True, verbatim=True, rule_supported=True,
        )
        assert confidence.band is ConfidenceBand.HIGH

        score, floored = apply_importance_floor(0.1, assessment.level)
        assert floored and score == CRITICAL_IMPORTANCE_FLOOR

        assert not dismissal_policy(assessment.level).allows_bulk

    async def test_internal_detail_does_not_leak_to_the_patient_path(self):
        """
        Internal notes are audited; patient messages are gated. A conflict quote
        that is legitimate internally must not pass the patient grounding check
        just because it exists somewhere in the record.
        """
        result = screen_patient_draft(
            "Your nurse recorded Lisinopril 100mg.",
            ["Increased Lisinopril to 10mg daily."],
        )
        assert result.verdict is GateVerdict.BLOCKED_UNGROUNDED
