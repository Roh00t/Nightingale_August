"""
/api/ai/conflicts — the single implementation of contradiction detection.

Detection used to run in two places: this Python module and a hand-maintained
TypeScript port in the frontend. Nothing enforced that they agreed, so they
could drift until one flagged a dosing contradiction and the other did not. The
port is gone; these tests cover the endpoint that replaced it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from services.auth import CallerIdentity, require_caller
from tests.support.pgharness import CLINIC_1


@pytest.fixture
def client():
    async def _caller() -> CallerIdentity:
        return CallerIdentity(
            user_id="00000000-0000-0000-0000-000000000001",
            role="clinician", clinic_id=CLINIC_1, display_name="Dr. Test",
        )
    main.app.dependency_overrides[require_caller] = _caller
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def entry(eid, author, role, text, ts="2026-01-01T00:00:00+00:00"):
    return {
        "id": eid, "author_id": author, "author_role": role,
        "content_text": text, "created_at": ts,
    }


def post(client, entries, **kw):
    return client.post(
        "/api/ai/conflicts",
        json={"entries": entries, **kw},
        headers={"Authorization": "Bearer stub"},
    )


CROSS_AUTHOR = [
    entry("e1", "cA", "clinician",
          "Prescribed Lisinopril 10mg daily. Patient is allergic to penicillin."),
    entry("e2", "sB", "staff",
          "Administered Lisinopril 100mg as charted. Chart says not allergic to penicillin.",
          "2026-01-02T00:00:00+00:00"),
]


class TestConflictsEndpoint:
    def test_requires_authentication(self):
        """Guardrail S6: no AI endpoint is open."""
        assert TestClient(main.app).post(
            "/api/ai/conflicts", json={"entries": []}
        ).status_code == 401

    def test_detects_dosage_and_allergy_with_allergy_ranked_first(self, client):
        body = post(client, CROSS_AUTHOR).json()
        classes = [c["conflict_class"] for c in body["conflicts"]]

        assert len(body["conflicts"]) == 2, classes
        assert classes[0] == "allergy", "allergy must rank first"
        assert "dosage" in classes
        assert body["has_critical"] is True
        assert body["entries_scanned"] == 2

    def test_response_schema_is_complete(self, client):
        """L1: every field the UI renders is present and correctly typed."""
        for c in post(client, CROSS_AUTHOR).json()["conflicts"]:
            assert set(c) == {
                "conflict_class", "entity", "severity",
                "requires_human_resolution", "claims",
            }
            assert c["conflict_class"] in {"allergy", "dosage", "medication", "vital"}
            assert c["severity"] in {"critical", "high"}
            assert c["requires_human_resolution"] is True
            assert len(c["claims"]) >= 2
            for claim in c["claims"]:
                assert set(claim) >= {"author_role", "entry_id", "value", "quote"}
                assert claim["quote"].strip(), "a claim was returned without its quote"

    def test_never_arbitrates(self, client):
        """The endpoint reports the delta; it never picks a winner."""
        body = post(client, CROSS_AUTHOR).json()
        assert all(c["requires_human_resolution"] for c in body["conflicts"])
        # No field anywhere names a winner or a resolution.
        blob = str(body).lower()
        for forbidden in ["winner", "resolved_value", "correct_value", "recommended"]:
            assert forbidden not in blob

    def test_spelled_out_dosage_is_normalised(self, client):
        """Obfuscated contradiction: prose against digits."""
        body = post(client, [
            entry("e1", "cA", "clinician", "Prescribed Lisinopril one hundred mg daily."),
            entry("e2", "cB", "clinician", "Dispensed Lisinopril 10mg daily.",
                  "2026-01-02T00:00:00+00:00"),
        ]).json()
        dosage = [c for c in body["conflicts"] if c["conflict_class"] == "dosage"]
        assert dosage, "spelled-out dosage contradiction missed"
        assert {c["value"] for c in dosage[0]["claims"]} == {"100mg", "10mg"}

    def test_same_author_revision_is_not_reported(self, client):
        body = post(client, [
            entry("e1", "cA", "clinician", "Started Lisinopril 10mg."),
            entry("e2", "cA", "clinician", "Increased Lisinopril to 20mg.",
                  "2026-02-01T00:00:00+00:00"),
        ]).json()
        assert body["conflicts"] == []
        assert body["has_critical"] is False

    def test_same_author_can_be_opted_into(self, client):
        """The caller may ask for self-contradictions explicitly."""
        body = post(client, [
            entry("e1", "cA", "clinician", "Started Lisinopril 10mg."),
            entry("e2", "cA", "clinician", "Increased Lisinopril to 20mg.",
                  "2026-02-01T00:00:00+00:00"),
        ], include_same_author=True).json()
        assert len(body["conflicts"]) == 1

    def test_empty_and_textless_entries_are_safe(self, client):
        assert post(client, []).json() == {
            "conflicts": [], "entries_scanned": 0, "has_critical": False
        }
        body = post(client, [entry("e1", "cA", "clinician", None)]).json()
        assert body["conflicts"] == []
        assert body["entries_scanned"] == 1

    def test_agreement_produces_no_conflict(self, client):
        body = post(client, [
            entry("e1", "cA", "clinician", "Lisinopril 10mg daily."),
            entry("e2", "sB", "staff", "Confirmed Lisinopril 10mg daily.",
                  "2026-01-02T00:00:00+00:00"),
        ]).json()
        assert body["conflicts"] == []

    def test_logs_counts_not_quotes(self, client, caplog):
        """L2: the log must not become a second copy of the clinical text."""
        import logging
        with caplog.at_level(logging.INFO, logger="routers.conflicts"):
            post(client, CROSS_AUTHOR)
        blob = "\n".join(r.getMessage() for r in caplog.records)
        assert "Lisinopril" not in blob
        assert "penicillin" not in blob
        assert "contradiction" in blob


class TestAuthFailureModes:
    """
    A bad credential is a client error, not a service outage.

    Returning 503 for a malformed token would tell a caller the service is
    broken and would page an on-call engineer for what is simply an invalid
    credential. These separate the two.
    """

    def test_missing_token_is_401(self):
        assert TestClient(main.app).post(
            "/api/ai/conflicts", json={"entries": []}
        ).status_code == 401

    @pytest.mark.parametrize("bad", [
        "not.a.token", "garbage", "a.b", "...", "Bearer",
        "eyJhbGciOiJIUzI1NiJ9.notbase64.sig",
    ])
    def test_malformed_token_is_401_not_500_or_503(self, bad):
        r = TestClient(main.app).post(
            "/api/ai/conflicts",
            json={"entries": []},
            headers={"Authorization": f"Bearer {bad}"},
        )
        assert r.status_code == 401, f"{bad!r} produced {r.status_code}"

    def test_jwk_set_selects_the_key_matching_the_token_kid(self):
        """
        Supabase publishes a JWK *Set* and rotates within it, so the token's
        `kid` selects the key. Passing the whole set to `from_jwk` fails with an
        opaque JSON error — which is exactly how this surfaced, because the path
        never ran until real credentials existed.
        """
        from services.auth import _select_jwk

        document = {"keys": [
            {"kid": "old", "kty": "EC", "alg": "ES256"},
            {"kid": "current", "kty": "EC", "alg": "ES256"},
        ]}
        assert _select_jwk(document, "current")["kid"] == "current"
        assert _select_jwk({"kid": "solo", "kty": "EC"}, None)["kid"] == "solo"
        assert _select_jwk({"keys": [{"kid": "only", "kty": "EC"}]}, None)["kid"] == "only"

    def test_unknown_kid_is_rejected_rather_than_guessed(self):
        """After rotation, an unmatched kid must fail loudly."""
        from services.auth import _select_jwk

        with pytest.raises(RuntimeError, match="rotated|matches"):
            _select_jwk({"keys": [{"kid": "a"}, {"kid": "b"}]}, "missing")

    def test_ambiguous_key_set_without_kid_is_rejected(self):
        from services.auth import _select_jwk

        with pytest.raises(RuntimeError, match="multiple keys"):
            _select_jwk({"keys": [{"kid": "a"}, {"kid": "b"}]}, None)
