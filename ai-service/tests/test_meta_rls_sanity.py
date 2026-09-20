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


def test_the_migration_chain_applied_in_full(pg):
    """The harness must build the schema that SHIPS, not a subset of it.

    Until 21 Sep 2026 it applied 001_foundation.sql alone. That hid a live
    defect: stamp_interaction_log was fixed on 3 Sep in a later migration, the
    harness never applied that file, and 487 green tests ran against the broken
    version. A fix the tests cannot see is not a tested fix.

    Skips are permitted only for a Postgres feature this engine lacks, and only
    the ones listed here. A new name appearing means either a broken migration
    or a widening gap between the test engine and the deployed one — both worth
    failing over.
    """
    from tests.support.pgharness import harness_server_version

    version = harness_server_version(pg.dsn)
    skipped = {name for name, _ in pg.skipped_migrations}

    # security_invoker on views is PostgreSQL 15+. Supabase deploys 17.
    known_version_gated = (
        {"20260903140000_ui_telemetry_views.sql"} if version < 15 else set()
    )

    unexpected = skipped - known_version_gated
    assert not unexpected, (
        f"Migrations failed to apply in the harness (PostgreSQL {version}): "
        f"{sorted(unexpected)}. Details: {pg.skipped_migrations}"
    )

    if known_version_gated & skipped:
        # Not a failure, but it must not be silent: the telemetry dashboard's
        # tenant isolation rests on security_invoker and is NOT under test here.
        import warnings

        warnings.warn(
            f"Harness is PostgreSQL {version}; Supabase deploys 17. "
            f"Skipped {sorted(known_version_gated & skipped)} — the "
            f"security_invoker views are unverified on this engine. "
            f"Install postgresql@17 for parity.",
            stacklevel=1,
        )


def test_interaction_log_metadata_allowlist_is_live(clinician_client, user_ids):
    """The forgery this whole exercise exists for.

    stamp_interaction_log is SECURITY DEFINER, so its original guard
    (`current_user NOT IN ('authenticated','anon')`) compared the FUNCTION OWNER,
    never the caller — it always early-returned and the trigger never fired. A
    clinician could therefore record a role they do not hold, and write arbitrary
    free text into a column the PHI posture treats as metadata-only.

    Demonstrated against a live database on 3 Sep, fixed the same day in
    20260903130000_fix_stamp_trigger_guards.sql, and unverifiable until today
    because the harness applied 001_foundation.sql alone.
    """
    clinician_client.table("interaction_log").insert({
        "user_id": user_ids["clinician"],
        "user_role": "admin",                      # a role the caller does not hold
        "action_type": "accept",
        "target_type": "highlight",
        "target_id": "11111111-2222-3333-4444-555555555555",
        "target_metadata": {
            "keywords": ["x"],
            "secret_note": "Patient Alice Wong has HIV",
            "injected": True,
        },
    }).execute()

    row = (
        clinician_client.table("interaction_log")
        .select("user_role, target_metadata")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data[0]
    )

    assert row["user_role"] == "clinician", (
        "user_role was caller-supplied — the learning loop's view of who did "
        "what is forgeable by its own subjects"
    )
    assert row["target_metadata"] == {"keywords": ["x"]}, (
        f"metadata allowlist did not strip: {row['target_metadata']}. Free "
        f"text — including PHI — persisted in a column documented as "
        f"metadata-only."
    )
