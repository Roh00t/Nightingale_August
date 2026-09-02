-- CRITICAL: stop a patient promoting themselves to clinician.
--
-- THE VULNERABILITY.
--
-- `001_foundation.sql` grants `"Users can update their own profile"` as
-- `FOR UPDATE USING (id = auth.uid())` with no WITH CHECK and no column
-- restriction. A row you may update is therefore a row you may update ANY column
-- of — including `role`, the column every other policy keys on:
--
--     UPDATE profiles SET role='clinician' WHERE id = auth.uid();   -- succeeded
--
-- Measured against a seeded cluster, same JWT before and after that one
-- statement: care_note_assessments 0 -> 1, internal timeline_entries 0 -> 7,
-- comments 0 -> 3, note_versions 0 -> 4. It defeats the care_note_assessments
-- split, the visibility='internal' exclusion and the patient-facing gate at once.
--
-- WHY A TRIGGER RATHER THAN A WITH CHECK.
--
-- A WITH CHECK would have to compare against the caller's current role, which
-- means calling get_user_role() — a SECURITY DEFINER read of this same table,
-- mid-UPDATE, on the row being written. The semantics are subtle and easy to get
-- wrong under a future edit. A trigger compares OLD to NEW directly, which needs
-- no lookup and cannot be misread. It also matches the pattern already used by
-- set_clinic_id_from_care_note() and strip_internal_glance_keys().
--
-- WHICH CALLERS ARE RESTRICTED, AND WHY IT IS SPELLED THIS WAY.
--
-- The report's draft tested `current_setting('role') = 'service_role'`. Measured
-- in the harness, that setting is 'none' for the migration/owner connection, so
-- that form would have blocked seeding and any admin re-assignment while looking
-- correct.
--
-- PostgREST maps every JWT to exactly one of two Postgres roles: `authenticated`
-- for a signed-in user and `anon` for an unauthenticated one. Those are the only
-- roles an attacker can reach with a token, so those are the roles restricted.
-- service_role, the table owner and superusers keep provisioning and
-- re-assignment, which they need.
--
-- Idempotent.

BEGIN;

CREATE OR REPLACE FUNCTION pin_profile_identity_columns()
RETURNS trigger AS $$
BEGIN
  -- Only end-user sessions are restricted. Anything else is a trusted
  -- server-side path: seeding, migrations, admin re-assignment, and the
  -- service-role writes that provisioning depends on.
  IF current_user NOT IN ('authenticated', 'anon') THEN
    RETURN NEW;
  END IF;

  -- Raise rather than silently pin. A silent revert would let a UI report
  -- success for a change that did not happen, and would hide an attempt that a
  -- clinic would want to see in its logs.
  IF NEW.role IS DISTINCT FROM OLD.role THEN
    RAISE EXCEPTION
      'role may not be changed by the account holder (attempted % -> %)',
      OLD.role, NEW.role
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  IF NEW.clinic_id IS DISTINCT FROM OLD.clinic_id THEN
    RAISE EXCEPTION 'clinic_id may not be changed by the account holder'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_pin_profile_identity ON profiles;

CREATE TRIGGER trg_pin_profile_identity
  BEFORE UPDATE ON profiles
  FOR EACH ROW EXECUTE FUNCTION pin_profile_identity_columns();

COMMIT;
