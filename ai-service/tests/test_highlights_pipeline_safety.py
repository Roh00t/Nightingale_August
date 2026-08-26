"""
The safety layer, exercised through the actual /api/ai/highlights route.

The modules in services/safety are unit-tested in test_clinical_safety.py. That
proves they work; it does not prove the pipeline calls them. This suite drives
the real FastAPI route with the LLM stubbed, so a regression that quietly
bypasses extraction verification or the risk floor fails here.

Only the Groq call and the JWT dependency are replaced. Redaction, extraction
validation, risk floors, confidence, abstention and importance floors all run
for real.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from services.auth import CallerIdentity, require_caller
from tests.support.pgharness import CLINIC_1

SOURCE_ENTRY = (
    "Lab results review: eGFR dropped to 45 (from 58 in June). "
    "Potassium 5.1 (borderline high). Patient developed anaphylaxis after the first dose."
)


@pytest.fixture
def client(monkeypatch):
    """TestClient with auth satisfied and the LLM stubbed at the router boundary."""
    async def _caller() -> CallerIdentity:
        return CallerIdentity(
            user_id="00000000-0000-0000-0000-000000000001",
            role="clinician",
            clinic_id=CLINIC_1,
            display_name="Dr. Test",
        )

    main.app.dependency_overrides[require_caller] = _caller
    # No interaction history: learned weight resolves to its neutral prior.
    from services.importance import set_interaction_source

    set_interaction_source(lambda clinic_id, limit=200: [])
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()
    set_interaction_source(None)


def _stub_llm(monkeypatch, highlights):
    async def _fake(entries):
        return highlights
    monkeypatch.setattr("routers.highlights.generate_highlights", _fake)


def _post(client, entry_text=SOURCE_ENTRY):
    return client.post(
        "/api/ai/highlights",
        json={
            "entries": [
                {"content": entry_text, "entry_type": "manual_note",
                 "created_at": "2026-01-15T11:00:00+00:00", "entry_id": "entry-1"}
            ]
        },
        headers={"Authorization": "Bearer stub"},
    )


class TestPipelineRunsTheSafetyLayer:
    def test_verbatim_claim_is_surfaced_with_confidence_and_span(self, client, monkeypatch):
        _stub_llm(monkeypatch, [{
            "content_snippet": "eGFR dropped to 45",
            "risk_reason": "Declining renal function",
            "risk_level": "low",
            "importance_score": 0.5,
            "provenance_pointer": "Entry 1",
        }])
        body = _post(client).json()
        assert len(body["highlights"]) == 1
        h = body["highlights"][0]

        # Confidence is measured and present, not absent or self-reported.
        assert h["confidence_score"] is not None
        assert h["confidence_band"] in {"high", "medium", "low"}
        assert set(h["safety_metadata"]["confidence_components"]) == {
            "agreement", "verification", "rule_support"
        }

        # Provenance resolves to a real span in the source entry.
        span = h["provenance_pointer"]["span"]
        assert SOURCE_ENTRY[span["from"]:span["to"]] == "eGFR dropped to 45"
        assert h["provenance_pointer"]["source_id"] == "entry-1"

    def test_hallucinated_claim_is_dropped_before_it_reaches_the_client(
        self, client, monkeypatch
    ):
        """A snippet that is not a verbatim span of the source never surfaces."""
        _stub_llm(monkeypatch, [{
            "content_snippet": "kidney function has deteriorated catastrophically",
            "risk_reason": "invented",
            "risk_level": "critical",
            "importance_score": 0.9,
            "provenance_pointer": "Entry 1",
        }])
        assert _post(client).json()["highlights"] == []

    def test_deterministic_floor_overrides_a_low_model_proposal(self, client, monkeypatch):
        """The model said 'low' on text containing anaphylaxis. Rules win."""
        _stub_llm(monkeypatch, [{
            "content_snippet": "Patient developed anaphylaxis after the first dose",
            "risk_reason": "Allergic reaction",
            "risk_level": "low",
            "importance_score": 0.2,
            "provenance_pointer": "Entry 1",
        }])
        h = _post(client).json()["highlights"][0]
        assert h["risk_level"] == "critical"
        assert h["model_risk"] == "low"
        assert h["risk_floor"] == "critical"
        assert any(
            r["name"] == "anaphylaxis" for r in h["safety_metadata"]["triggered_rules"]
        )

    def test_critical_finding_gets_the_importance_floor(self, client, monkeypatch):
        """Learned weight cannot bury a critical item below 0.90."""
        _stub_llm(monkeypatch, [{
            "content_snippet": "Patient developed anaphylaxis after the first dose",
            "risk_reason": "Allergic reaction",
            "risk_level": "low",
            "importance_score": 0.05,
            "provenance_pointer": "Entry 1",
        }])
        h = _post(client).json()["highlights"][0]
        assert h["importance_score"] >= 0.90
        assert h["safety_metadata"]["importance_floor_applied"] is True

    def test_single_shot_verbatim_claim_lands_exactly_on_the_threshold(
        self, client, monkeypatch
    ):
        """
        Documents a real limitation of running without an ensemble.

        With no sampling the agreement term is the neutral 0.5 prior, so a
        byte-exact claim with no rule support scores:

            0.50 x 0.5  +  0.35 x 1.0  +  0.15 x 0  =  0.60

        which is exactly the abstention threshold, and therefore surfaces as
        'medium' rather than being withheld. In this configuration abstention
        only fires for claims that matched after normalisation (verification
        0.75 -> 0.51). Enabling ensemble sampling is what makes the confidence
        signal discriminating; it costs N x tokens per request.
        """
        _stub_llm(monkeypatch, [{
            "content_snippet": "from 58 in June",
            "risk_reason": "Historical value",
            "risk_level": "info",
            "importance_score": 0.1,
            "provenance_pointer": "Entry 1",
        }])
        h = _post(client).json()["highlights"][0]
        assert h["confidence_score"] == pytest.approx(0.60, abs=0.005)
        assert h["confidence_band"] == "medium"
        assert h["abstained"] is False

    def test_normalized_match_scores_below_threshold_and_is_withheld(
        self, client, monkeypatch
    ):
        """A claim that only matched after whitespace folding does abstain."""
        _stub_llm(monkeypatch, [{
            # Extra whitespace forces a normalised rather than exact match, and
            # this phrase triggers no risk rule, so there is no rule support to
            # lift the score back over the threshold.
            "content_snippet": "from   58   in   June",
            "risk_reason": "Historical value",
            "risk_level": "info",
            "importance_score": 0.1,
            "provenance_pointer": "Entry 1",
        }])
        # Verification 0.75 instead of 1.0 -> 0.25 + 0.2625 = 0.5125 -> abstain.
        # It is not critical, so it is withheld rather than surfaced flagged.
        assert _post(client).json()["highlights"] == []

    def test_endpoint_still_requires_authentication(self):
        """Guardrail S6 must survive the refactor."""
        assert TestClient(main.app).post("/api/ai/highlights", json={"entries": []}).status_code == 401
