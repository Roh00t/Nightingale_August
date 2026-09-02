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
