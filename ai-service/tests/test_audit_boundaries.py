"""
Pins the boundaries claimed in CLAUDE.md §8.

These are not feature tests. Each one asserts a *limitation* the audit states, so
that a future change which quietly narrows or widens it is caught. A documented
boundary that drifts is worse than an undocumented one, because people have
calibrated to it.

Where the audit says ABSENT, the test asserts absence. If someone later builds
the feature, the test fails and the documentation must be updated with it — which
is the point.
"""

from __future__ import annotations

import pathlib
import re

import pytest


REPO = pathlib.Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Scenario 3 — log scrubbing catches structured IDs, not free-text names
# ---------------------------------------------------------------------------


class TestLogScrubbingBoundary:
    def test_structured_identifiers_are_always_caught(self):
        from services.log_scrubbing import scrub

        for raw, token in [
            ("NRIC S1234567D on file", "<NRIC_REDACTED>"),
            ("call +6591234567 now", "<PHONE_REDACTED>"),
            ("email a.wong@clinic.sg", "<EMAIL_REDACTED>"),
        ]:
            assert token in scrub(raw), f"structured identifier leaked: {raw}"

    def test_free_text_names_are_NOT_caught(self):
        """
        The documented limit. Names are scrubbed only in labelled positions,
        because a bare capitalised-word rule eats "Lisinopril" and "Monday" and
        gets the filter switched off entirely.

        If this test starts failing, someone improved name detection — good, but
        CLAUDE.md §8.3 says otherwise and must be updated.
        """
        from services.log_scrubbing import scrub

        out = scrub("Patient Alice Wong was admitted")
        assert "Alice Wong" in out, (
            "free-text names are now scrubbed — CLAUDE.md §8 scenario 3 claims "
            "they are not, and the documentation is now wrong"
        )

    def test_labelled_names_are_caught(self):
        from services.log_scrubbing import scrub

        assert "<NAME_REDACTED>" in scrub('patient_name="Tan Wei Ming"')


# ---------------------------------------------------------------------------
# Scenario 6 / capability 4 — safety rules are English-only
# ---------------------------------------------------------------------------


class TestMultilingualDownstreamBoundary:
    def test_english_dose_triggers_a_risk_floor(self):
        from services.safety.risk_rules import assess_risk

        assert assess_risk("Potassium 6.4 mmol/L").triggered

    def test_code_switched_clinical_text_does_NOT_trigger_a_floor(self):
        """
        Capability 4 is MISSING and this is why: the deterministic floors are
        English regex. A code-switched utterance survives transcription and
        summarisation but reaches the safety layer unrecognised.

        Asserted so the gap is measurable rather than adjectival.
        """
        from services.safety.risk_rules import assess_risk

        assert not assess_risk("gula darah dia tinggi sangat").triggered, (
            "a non-English risk phrase now triggers a floor — capability 4 is no "
            "longer MISSING and the matrix must be updated"
        )


# ---------------------------------------------------------------------------
# Scenario 7 / capability 1 — transcription is whole-file
# ---------------------------------------------------------------------------


class TestNoStreamingASR:
    def test_transcription_buffers_the_whole_file_before_processing(self):
        """
        The allergy at minute two is known at minute twenty. Asserted at the
        source so 'we have streaming' cannot become true in documentation before
        it is true in code.
        """
        src = (REPO / "ai-service/routers/transcribe.py").read_text()
        # The only chunking present is the upload size cap, not incremental ASR.
        assert "_read_capped" in src
        for streaming_marker in ("partial_transcript", "interim_result", "on_partial"):
            assert streaming_marker not in src, (
                f"{streaming_marker} suggests streaming ASR exists — capability 1 "
                "is documented MISSING"
            )


# ---------------------------------------------------------------------------
# Scenario 5 / capability — no per-clinic settings
# ---------------------------------------------------------------------------


class TestClinicOnboardingIsConfigOnly:
    def test_no_schema_change_is_needed_for_a_second_clinic(self):
        """Every patient-scoped table already carries clinic_id."""
        sql = "\n".join(
            p.read_text() for p in sorted((REPO / "supabase/migrations").glob("*.sql"))
        )
        for table in ("timeline_entries", "highlights", "comments", "note_versions"):
            assert re.search(rf"ALTER TABLE\s+{table}\s+ADD COLUMN IF NOT EXISTS clinic_id", sql), \
                f"{table} has no clinic_id — onboarding would need a schema change"

    def test_per_clinic_settings_are_absent(self):
        """
        The documented gap: branding, timezone, templates and the Telegram bot
        identity are global, so Clinic B's patients get Clinic A's bot.
        """
        sql = "\n".join(
            p.read_text() for p in sorted((REPO / "supabase/migrations").glob("*.sql"))
        )
        assert "clinic_settings" not in sql, (
            "a clinic_settings table now exists — CLAUDE.md §8 scenario 5 says it "
            "does not and must be updated"
        )


# ---------------------------------------------------------------------------
# Scenario 13 — contradictions are surfaced, not reconciled
# ---------------------------------------------------------------------------


class TestBlanketDenialIsNotDetected:
    """
    Scenario 13, and the reason it is graded DOES NOT.

    The engine detects an allergy contradiction when both sides name the SAME
    drug with an explicit negation. It does not parse a blanket denial as
    contradicting a specific named allergen — which is exactly the scenario the
    brief describes: a nurse records "Penicillin allergy", the patient tells the
    AI "no known drug allergies".

    Measured, not assumed. These four cases were run before this test was written.
    """

    def _detect(self, a: str, b: str):
        from services.safety.clinical_conflict import detect_conflicts

        return detect_conflicts([
            {"id": "e1", "author_id": "n1", "author_role": "staff",
             "content_text": a, "created_at": "2026-01-01T00:00:00+00:00"},
            {"id": "e2", "author_id": None, "author_role": "system",
             "content_text": b, "created_at": "2026-01-02T00:00:00+00:00"},
        ])

    def test_same_drug_with_explicit_negation_IS_detected(self):
        """The form that works, and the one the existing suite covers."""
        assert self._detect(
            "Patient is allergic to penicillin.",
            "Chart says not allergic to penicillin.",
        )

    @pytest.mark.parametrize("nurse,ai", [
        ("Penicillin allergy documented.", "Patient reports no known drug allergies."),
        ("Allergy to penicillin documented.", "Patient reports no known drug allergies."),
        ("Patient is allergic to penicillin.", "Patient reports no known drug allergies."),
    ])
    def test_blanket_denial_is_NOT_detected(self, nurse, ai):
        """
        The clinical failure this pins. A blanket "no known allergies" does not
        register as contradicting a named allergen, so the highest-severity
        contradiction class silently produces nothing.

        When this is fixed, this test fails — and CLAUDE.md §8 scenario 13 and
        capability 10 must be regraded at the same time.
        """
        assert not self._detect(nurse, ai), (
            "blanket-denial contradictions are now detected — scenario 13 is no "
            "longer DOES NOT and the audit must be updated"
        )

    def test_no_precedence_is_applied(self):
        """
        Documented gap. A nurse's manual record and an unverified AI extraction
        are surfaced symmetrically; the engine reports the delta and a clinician
        decides.
        """
        src = (REPO / "ai-service/services/safety/clinical_conflict.py").read_text()
        assert "def apply_precedence" not in src


# ---------------------------------------------------------------------------
# Scenario 15 — fatigue resistance holds, exposure bias does not
# ---------------------------------------------------------------------------


class TestLearningLoopBoundary:
    def test_exposure_bias_correction_is_absent(self):
        """
        The loop only scores what it surfaced. Asserted so 'we handle exposure
        bias' cannot become true in a README before it is true in code.
        """
        src = (REPO / "ai-service/services/importance.py").read_text()
        for marker in ("epsilon", "exploration", "random.sample", "suppressed_sample"):
            assert marker not in src, (
                f"{marker} suggests exposure-bias sampling now exists — CLAUDE.md "
                "§8 scenario 15 and capability 12 must be updated"
            )

    def test_fatigue_resistance_is_real(self):
        from services.importance import ABSOLUTE_FLOOR, NO_DEMOTION_SEVERITIES

        assert ABSOLUTE_FLOOR.get("critical", 0) >= 0.90
        assert "high" in NO_DEMOTION_SEVERITIES


# ---------------------------------------------------------------------------
# Scenario 16 — staleness is marked, not diffed
# ---------------------------------------------------------------------------


class TestProvenanceStalenessBoundary:
    def test_intra_version_edit_is_detected(self):
        from services.provenance import quote_hash, verify_quote, ProvenanceVerdict

        src = "Lisinopril 10mg daily. Review in 3 months."
        h = quote_hash("Lisinopril 10mg daily.")
        assert verify_quote(
            stored_hash=h, source_text=src.replace("10mg", "100mg"),
            stored_version=3, current_version=3,
        ) is ProvenanceVerdict.MODIFIED

    def test_unknown_provenance_never_claims_currency(self):
        from services.provenance import verify_quote, ProvenanceVerdict

        assert verify_quote(stored_hash=None, source_text="anything") \
            is ProvenanceVerdict.UNVERIFIABLE

    def test_side_by_side_comparison_is_absent(self):
        """Documented gap: the badge marks staleness, it does not show the diff."""
        src = (REPO / "frontend/components/glance/CriticalFlags.tsx").read_text()
        assert "SOURCE EDITED" in src
        for marker in ("DiffView", "sideBySide", "originalText"):
            assert marker not in src, (
                "a side-by-side comparison now exists — scenario 16 says it does not"
            )


class TestPatientViewShowsOnlyApprovedContent:
    """The patient portal leak of 3 Sep 2026, pinned.

    A patient's own portal was rendering clinician-grade pipeline output — ASR
    confidence, entity counts, the mock-transcript badge and the full
    speaker-labelled transcript — alongside a care instruction reading
    "Lisinopril to 10 0000000mg daily" that no human had ever approved.

    Two separate defects wearing one screenshot:

      1. The capture result panel was not role-aware.
      2. `visibility = 'patient_visible'` was treated as approval. It is not.
         Rows predating the maker-checker gate carry no verdict and rendered
         identically to signed-off ones.

    These are STRUCTURAL assertions over source, not render tests. This repo has
    no harness that mounts the portal, so what is pinned here is that the gate
    is present in the code — not that a browser honours it. The plan's manual
    login check remains the only thing that proves the rendered page.
    """

    def test_voice_capture_result_panel_is_role_gated(self):
        src = (REPO / "frontend/components/voice/VoiceCapture.tsx").read_text()
        assert "const isPatient = userRole === 'patient'" in src, (
            "VoiceCapture must derive a patient flag to gate its result panel"
        )
        # The diagnostics that leaked, each behind the gate.
        for probe in ("ASR confidence", "identifiers removed", "mock transcript"):
            assert probe in src, f"expected {probe!r} to still exist for clinicians"
        assert "{!isPatient && (" in src, "diagnostics chips are not gated"
        # The raw diarized transcript block.
        assert "Speaker-labelled transcript" in src
        gate_at = src.index("{!isPatient && (\n            <details")
        assert gate_at > 0, "the speaker transcript block is not behind !isPatient"

    def test_patients_still_keep_the_recorder(self):
        """A fix that silently deletes patient voice capture is a worse outage.

        `patient_session` capture is a designed feature and /api/ai/transcribe
        authorises the patient role for it. Only the *rendering* of pipeline
        output changed.
        """
        src = (REPO / "frontend/components/voice/VoiceCapture.tsx").read_text()
        assert "patient_session" in src
        router = (REPO / "ai-service/routers/transcribe.py").read_text()
        assert "patient" in router, "patient must remain authorised to transcribe"

    def test_patient_timeline_filters_on_approval_not_visibility(self):
        rule = (REPO / "frontend/lib/patient_visibility.ts").read_text()
        assert "patient_gate_verdict === 'passed'" in rule
        assert "kind === 'retraction'" in rule, (
            "retraction notices carry no verdict and must not be filtered out"
        )
        assert "entry.is_retracted" in rule, (
            "a retracted entry must stay visible, struck through, with its reason"
        )
        ws = (REPO / "frontend/components/patient/PatientWorkspace.tsx").read_text()
        assert "isApprovedForPatient" in ws, "the patient branch does not apply the rule"

    def test_the_rule_is_documented_as_not_a_security_control(self):
        """Overclaiming this filter would be the dangerous outcome.

        It stops unapproved rows being rendered. What stops them being created
        is `AND visibility = 'internal'` on the care-team INSERT policies. If
        that caveat is ever deleted from the module, someone will mistake a
        client-side predicate for a boundary.
        """
        rule = (REPO / "frontend/lib/patient_visibility.ts").read_text()
        assert "NOT A SECURITY CONTROL" in rule
        assert "visibility = 'internal'" in rule

    def test_patient_card_renders_the_withdrawn_badge(self):
        """Found while fixing the leak: the clinician timeline had rendered
        [WITHDRAWN BY CARE TEAM] since retraction shipped, and the patient's own
        Care Instructions card had not — so the one person who acted on a wrong
        dose was the one person not told it was withdrawn.
        """
        ws = (REPO / "frontend/components/patient/PatientWorkspace.tsx").read_text()
        assert "[WITHDRAWN BY CARE TEAM]" in ws
        assert "line-through" in ws, "withdrawn text must be struck through, not hidden"

    def test_the_two_withdrawal_treatments_are_deliberately_different(self):
        """The patient's is loud; the clinician's is quiet. That is the design.

        A clinician scrolling a timeline meets every withdrawal ever issued, and
        rendering each as a red slab is how red stops meaning danger — it has to
        compete with the critical-flags panel, which is a genuine one. The
        patient sees one message and has to be stopped from acting on it.

        This test exists so a future "let's make these consistent" change fails
        loudly rather than silently re-opening the 3 Sep bug from the other
        direction. Consistency is the wrong goal here.
        """
        ws = (REPO / "frontend/components/patient/PatientWorkspace.tsx").read_text()
        te = (REPO / "frontend/components/timeline/TimelineEntry.tsx").read_text()

        # Patient side: full red, uppercase, imperative.
        assert "bg-red-600" in ws, "the patient's withdrawal badge must stay red"
        assert "Do not follow this message." in ws, (
            "the patient needs an instruction, not just a label"
        )

        # Clinician side: muted and collapsed, but never silent or hidden.
        assert "bg-red-600" not in te, (
            "the clinician timeline's withdrawal treatment should be muted; "
            "red is reserved for active clinical danger"
        )
        assert "Withdrawn by care team" in te, (
            "muted is not the same as unstated — guardrails UI-1 requires words"
        )
        assert "line-through" in te, "the withdrawn entry must still be struck through"
        assert "retraction_reason" in te, "the reason must remain reachable"

    def test_seeded_patient_visible_row_does_not_claim_a_human_approved_it(self):
        """The seed stamps a verdict so the demo timeline is not empty. It must
        not also claim a clinician signed off, because nobody did.
        """
        sql = (REPO / "supabase/migrations/001_foundation.sql").read_text()
        assert '"patient_gate_verdict": "passed", "human_approved": false, "seeded": true' in sql


class TestOfflineBannerIsNotGatedOnConflictCount:
    """Scenario 9's offline banner was dead in the case it exists for.

    `TopCard.tsx` rendered the "Offline Mode (Rule-Derived)" alert *inside*
    `{conflictCount > 0 && ...}`. When the AI is unreachable the contradiction
    check never runs, so `conflictCount` is 0 — so the banner never appeared,
    and the clinician read a clean Glance View as "nothing found" rather than
    "not checked". Those are opposite clinical actions, which is the exact
    failure guardrails UI-1 exists to prevent.

    Structural assertion over source: this repo has no harness that renders
    TopCard, so what is pinned is the nesting, not the painted pixel.
    """

    def _src(self):
        return (REPO / "frontend/components/glance/TopCard.tsx").read_text()

    def test_degraded_banner_precedes_the_conflict_conditional(self):
        src = self._src()
        degraded_at = src.index("{aiDegraded && (")
        conflict_at = src.index("{conflictCount > 0 && (")
        assert degraded_at < conflict_at, (
            "the aiDegraded banner must render before, and independently of, the "
            "conflict banner — nesting it inside makes it dead when conflictCount is 0"
        )

    def test_degraded_banner_is_not_inside_the_conflict_card(self):
        """The precedence check alone would pass if someone nested it the other way."""
        src = self._src()
        conflict_open = src.index("{conflictCount > 0 && (")
        # The conflict block ends at its CardContent close; everything the
        # aiDegraded alert needs must sit outside that span.
        conflict_close = src.index("</Card>", conflict_open)
        degraded_at = src.index("{aiDegraded && (")
        assert not (conflict_open < degraded_at < conflict_close), (
            "aiDegraded is nested inside the conflictCount Card again"
        )

    def test_the_wording_guardrails_ui1_requires_is_intact(self):
        src = self._src()
        assert "Offline Mode (Rule-Derived)" in src
        assert "Absence of a flag does not imply absence of clinical concern." in src


class TestRedIsReservedForClinicalDanger:
    """guardrails.md UI-3, pinned.

    An incomplete care-plan item once rendered `border-red-400 bg-red-50` —
    louder than the critical-flags panel's own `border-red-200/60 bg-red-50/50`.
    A clinician scanning for danger met fifteen red boxes meaning "not ticked"
    before reaching one meaning "eGFR is falling". The cost of that lands on the
    one alert that mattered.

    These assertions are narrow on purpose: they name the specific patterns that
    caused the problem rather than banning the string "red-", which would fail on
    every legitimate use.
    """

    @staticmethod
    def _code_only(path):
        """Strip comments before asserting.

        The first version of this test failed on a comment that *documented* the
        removed pattern — the assertion was reading prose, not code. Same trap as
        grepping a docstring for the thing the docstring describes.
        """
        import re
        src = (REPO / path).read_text()
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        src = re.sub(r"(?m)^\s*//.*$", "", src)
        return src

    def test_care_plan_rows_are_not_red(self):
        for rel in (
            "frontend/components/patient/CarePlanCard.tsx",
            "frontend/components/patient/PatientWorkspace.tsx",
            "frontend/components/glance/TopCard.tsx",
        ):
            src = self._code_only(rel)
            assert "border-red-400 bg-red-50" not in src, (
                f"{rel}: an unticked checkbox is not a clinical danger (UI-3)"
            )
            assert "bg-red-50 text-red-600" not in src, (
                f"{rel}: a low completion score is not a clinical danger (UI-3)"
            )
            assert "'bg-red-500'" not in src, (
                f"{rel}: a progress bar is not a clinical danger (UI-3)"
            )

    def test_topcard_carries_no_red_at_all(self):
        """TopCard is the Glance View. Its only red was decorative — reject
        buttons and incomplete markers — and CriticalFlags, which it renders,
        owns the genuine red."""
        src = self._code_only("frontend/components/glance/TopCard.tsx")
        assert "red-" not in src, (
            "TopCard should carry no red; critical findings render through "
            "CriticalFlags, which does"
        )

    def test_the_genuine_red_uses_survive(self):
        """The rule is a reservation, not a ban. If these ever go quiet, the
        de-escalation went too far."""
        flags = (REPO / "frontend/components/glance/CriticalFlags.tsx").read_text()
        assert "red-" in flags, "critical flags must stay red"
        ws = (REPO / "frontend/components/patient/PatientWorkspace.tsx").read_text()
        assert "bg-red-600" in ws, "the patient's withdrawal notice must stay red"

    def test_one_green_not_two(self):
        """emerald-* and green-* were both in use for the same 'verified/good'
        semantics. getTrustBadgeStyle defines the provenance language in green,
        so green is canonical."""
        import pathlib as _p
        for f in (REPO / "frontend").rglob("*.tsx"):
            if "node_modules" in str(f):
                continue
            assert "emerald-" not in f.read_text(), (
                f"{f.relative_to(REPO)}: use green-* — one token for verified/good"
            )
