"""
Shared test fixtures for Nightingale AI service tests.

These tests use the Supabase API with real JWT tokens for each role
to verify RLS policies, revision history, and AI features.
"""

import os
from pathlib import Path
import pytest
import httpx
from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment from root .env file
REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL", "http://localhost:54321")
SUPABASE_ANON_KEY = os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://localhost:8000")

# Fixed UUIDs from seed data
CLINIC_1_ID = "c0000000-0000-0000-0000-000000000001"
CLINIC_2_ID = "c0000000-0000-0000-0000-000000000002"

# Demo user credentials
DEMO_USERS = {
    "clinician": {
        "email": os.getenv("TEST_CLINICIAN_EMAIL", "dr.chen@nightingale.demo"),
        "password": os.getenv("TEST_CLINICIAN_PASSWORD", "demo-clinician-2026"),
    },
    "staff": {
        "email": os.getenv("TEST_STAFF_EMAIL", "nurse.james@nightingale.demo"),
        "password": os.getenv("TEST_STAFF_PASSWORD", "demo-staff-2026"),
    },
    "patient": {
        "email": os.getenv("TEST_PATIENT_EMAIL", "alice.wong@nightingale.demo"),
        "password": os.getenv("TEST_PATIENT_PASSWORD", "demo-patient-2026"),
    },
    "admin": {
        "email": os.getenv("TEST_ADMIN_EMAIL", "maria.santos@nightingale.demo"),
        "password": os.getenv("TEST_ADMIN_PASSWORD", "demo-admin-2026"),
    },
}


@pytest.fixture
def service_client() -> Client:
    """Supabase client with service role key (bypasses RLS)."""
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


@pytest.fixture
def anon_client() -> Client:
    """Supabase client with anon key (subject to RLS)."""
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


async def _get_authenticated_client(role: str) -> Client:
    """Create a Supabase client authenticated as a specific role."""
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    creds = DEMO_USERS[role]
    result = client.auth.sign_in_with_password({
        "email": creds["email"],
        "password": creds["password"],
    })
    return client


@pytest.fixture
async def clinician_client() -> Client:
    """Supabase client authenticated as clinician (Dr. Sarah Chen)."""
    return await _get_authenticated_client("clinician")


@pytest.fixture
async def staff_client() -> Client:
    """Supabase client authenticated as staff (Nurse James)."""
    return await _get_authenticated_client("staff")


@pytest.fixture
async def patient_client() -> Client:
    """Supabase client authenticated as patient (Alice Wong)."""
    return await _get_authenticated_client("patient")


@pytest.fixture
async def admin_client() -> Client:
    """Supabase client authenticated as admin (Maria Santos)."""
    return await _get_authenticated_client("admin")


@pytest.fixture
def ai_client() -> httpx.AsyncClient:
    """HTTP client for the AI service."""
    return httpx.AsyncClient(base_url=AI_SERVICE_URL)


@pytest.fixture
async def sample_care_note_id(patient_client) -> str:
    """Get the care note ID for the demo patient."""
    patient_user_id = (patient_client.auth.get_user()).user.id
    result = (
        patient_client.table("care_notes")
        .select("id")
        .eq("patient_id", patient_user_id)
        .limit(1)
        .execute()
    )
    assert len(result.data) > 0, "No care notes found - run seed data first"
    return result.data[0]["id"]


@pytest.fixture
async def sample_timeline_entries(clinician_client, sample_care_note_id) -> list:
    """Get timeline entries for the demo care note."""
    result = (
        clinician_client.table("timeline_entries")
        .select("*")
        .eq("care_note_id", sample_care_note_id)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data


@pytest.fixture
async def sample_highlights(clinician_client, sample_care_note_id) -> list:
    """Get highlights for the demo care note."""
    result = (
        clinician_client.table("highlights")
        .select("*")
        .eq("care_note_id", sample_care_note_id)
        .order("importance_score", desc=True)
        .execute()
    )
    return result.data
