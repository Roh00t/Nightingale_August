"""
Shared fixtures for the Nightingale micro-test suites.

These tests run against a REAL database — an ephemeral PostgreSQL cluster built
from supabase/migrations/001_foundation.sql and seeded through the real
seed_demo_data function. Nothing is mocked, and no credentials are required, so
`pytest tests/ -v` works on a clean checkout.

Every role fixture is subject to Row Level Security: statements execute as the
non-superuser `authenticated` role with `request.jwt.claim.sub` set to that
user, which is how Supabase evaluates a JWT. That is what makes the access
control assertions meaningful rather than vacuous.

If the Postgres binaries are unavailable the database-backed suites skip with a
clear reason; the pure-unit suites (redaction, provenance) still run.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from tests.support.pgclient import PgClient
from tests.support.pgharness import CLINIC_1, CLINIC_2, USERS, PgHarness, postgres_available

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://localhost:8000")

__all__ = ["CLINIC_1", "CLINIC_2", "USERS"]


# ---------------------------------------------------------------------------
# Cluster lifecycle
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg() -> PgHarness:
    """Start one cluster for the whole session and tear it down after."""
    if not postgres_available():
        pytest.skip(
            "PostgreSQL binaries (initdb/pg_ctl) not on PATH. "
            "Install with `brew install postgresql@14` to run the database suites."
        )
    harness = PgHarness()
    harness.start()
    harness.build()
    yield harness
    harness.stop()


@pytest.fixture(autouse=True)
def _reset_between_tests(request) -> None:
    """
    Restore seed state after any test that writes.

    Only applies to tests that actually took a database fixture, so the pure
    unit suites pay nothing for it.
    """
    yield
    if "pg" in request.fixturenames:
        try:
            request.getfixturevalue("pg").reset_data()
        except Exception:  # pragma: no cover - teardown must not mask failures
            pass


def _client(pg: PgHarness, role: str) -> PgClient:
    return PgClient(pg.dsn, USERS[role][0])


# ---------------------------------------------------------------------------
# Role-scoped clients (RLS applies)
# ---------------------------------------------------------------------------


@pytest.fixture
def clinician_client(pg) -> PgClient:
    """Dr. Sarah Chen — Nightingale Family Clinic."""
    return _client(pg, "clinician")


@pytest.fixture
def staff_client(pg) -> PgClient:
    """Nurse James Rivera — Nightingale Family Clinic."""
    return _client(pg, "staff")


@pytest.fixture
def patient_client(pg) -> PgClient:
    """Alice Wong — Nightingale Family Clinic."""
    return _client(pg, "patient")


@pytest.fixture
def admin_client(pg) -> PgClient:
    """Maria Santos — Nightingale Family Clinic."""
    return _client(pg, "admin")


@pytest.fixture
def sunrise_clinician_client(pg) -> PgClient:
    """Dr. James Miller — Sunrise Medical Center. Used for cross-clinic denial."""
    return _client(pg, "sunrise_clinician")


@pytest.fixture
def sunrise_patient_client(pg) -> PgClient:
    """Robert Lee — Sunrise Medical Center."""
    return _client(pg, "sunrise_patient")


@pytest.fixture
def service_client(pg) -> PgClient:
    """
    Bypasses RLS, as the service-role key does.

    Use only to arrange fixtures or to observe ground truth — never to assert an
    access-control outcome, since it can see everything by construction.
    """
    return PgClient(pg.dsn, None, service_role=True)


@pytest.fixture
def anon_client(pg) -> PgClient:
    """No identity: auth.uid() is NULL, so every policy should deny."""
    return PgClient(pg.dsn, None)


# ---------------------------------------------------------------------------
# Convenience data fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def user_ids() -> dict[str, str]:
    return {name: uid for name, (uid, _) in USERS.items()}


@pytest.fixture
def sample_care_note_id(service_client) -> str:
    """Alice Wong's care note."""
    result = (
        service_client.table("care_notes")
        .select("id")
        .eq("patient_id", USERS["patient"][0])
        .limit(1)
        .execute()
    )
    assert result.data, "Seed data missing: no care note for the demo patient"
    return result.data[0]["id"]


@pytest.fixture
def sunrise_care_note_id(service_client) -> str:
    """Robert Lee's care note, in the other clinic."""
    result = (
        service_client.table("care_notes")
        .select("id")
        .eq("patient_id", USERS["sunrise_patient"][0])
        .limit(1)
        .execute()
    )
    assert result.data, "Seed data missing: no care note for the Sunrise patient"
    return result.data[0]["id"]


@pytest.fixture
def sample_timeline_entries(clinician_client, sample_care_note_id) -> list:
    result = (
        clinician_client.table("timeline_entries")
        .select("*")
        .eq("care_note_id", sample_care_note_id)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data


@pytest.fixture
def sample_highlights(clinician_client, sample_care_note_id) -> list:
    result = (
        clinician_client.table("highlights")
        .select("*")
        .eq("care_note_id", sample_care_note_id)
        .order("importance_score", desc=True)
        .execute()
    )
    return result.data
