"""Prove the shim does not bypass RLS: same query, different roles, different results."""
import pytest
from postgrest.exceptions import APIError

def test_meta_rls_actually_applies(patient_client, clinician_client, service_client,
                                   sunrise_clinician_client, sample_care_note_id):
    pat  = len(patient_client.table("timeline_entries").select("*").execute().data)
    clin = len(clinician_client.table("timeline_entries").select("*").execute().data)
    svc  = len(service_client.table("timeline_entries").select("*").execute().data)
    sun  = len(sunrise_clinician_client.table("timeline_entries").select("*").execute().data)
    print(f"\n  patient sees          {pat}")
    print(f"  clinician sees        {clin}")
    print(f"  sunrise clinician     {sun}")
    print(f"  service role sees     {svc}  (RLS bypassed)")
    assert pat == 1,  "patient should see only the patient_visible instruction"
    assert clin == 8, "clinician should see all 8 of Alice's entries"
    assert sun == 3,  "sunrise clinician should see only their own clinic's 3"
    assert svc == 11, "service role should see both clinics (8 + 3)"
    assert pat < clin < svc, "RLS is not differentiating roles"

def test_meta_write_denial_raises(patient_client, sample_care_note_id):
    """A policy-denied write must raise, not silently no-op."""
    with pytest.raises(APIError):
        patient_client.table("timeline_entries").insert({
            "care_note_id": sample_care_note_id, "author_role": "patient",
            "author_id": "a0000000-0000-0000-0000-000000000003",
            "entry_type": "manual_note", "content": {}, "content_text": "x",
            "visibility": "internal",
        }).execute()

def test_meta_anon_sees_nothing(anon_client):
    assert anon_client.table("timeline_entries").select("*").execute().data == []
    assert anon_client.table("care_notes").select("*").execute().data == []
