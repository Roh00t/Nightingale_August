-- Nightingale — migrations NOT yet applied to production, in dependency order.
--
-- Verified against the live database on 2 Sep 2026: the 20260901 chain is
-- already applied; these three are not. Every statement is idempotent, so
-- running this whole file is safe even if part of it already landed.
--
-- MIGRATION 1 BELOW IS THE CRITICAL ONE. Until it runs, any patient can
-- execute  UPDATE profiles SET role='clinician' WHERE id=auth.uid()  and
-- read the entire internal record. Apply it even if you skip the rest.

-- ============================================================================
-- 20260902000001_pin_profile_identity.sql
-- ============================================================================
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

-- ============================================================================
-- 20260902000003_telegram_identity.sql
-- ============================================================================
-- Telegram as a delivery channel and a passwordless identity path.
--
-- THE CONSTRAINT THAT SHAPES THIS.
--
-- The Telegram Bot API **cannot message a phone number**. A bot may only send to
-- a `chat_id`, and a chat_id exists only after the person has opened a
-- conversation with the bot themselves. There is no API to initiate contact.
--
-- That is not a limitation to work around — it is the consent model, and it is
-- why the flow is a deep link rather than an outbound send:
--
--   1. Front desk generates a token and shows/sends `t.me/<Bot>?start=<token>`.
--   2. The patient taps it and presses Start. Telegram delivers `/start <token>`
--      to our webhook **with** their chat_id.
--   3. We bind that chat_id to the profile the token belongs to, and only then
--      can the clinic message them.
--
-- So a patient with no email and only a phone number is reachable — but only
-- after one deliberate tap. Any design that claims otherwise is describing
-- something the platform does not do.
--
-- Idempotent.

BEGIN;

-- Telegram's chat_id is a signed 64-bit integer, and for channels it can exceed
-- int4. Stored as bigint rather than text so an ordering or equality bug cannot
-- silently match the wrong chat.
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS telegram_chat_id bigint;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS telegram_linked_at timestamptz;

-- One chat per profile per clinic, and one profile per chat. Without this a
-- shared family handset could end up bound to two patients, and a message
-- intended for one would reach the other.
CREATE UNIQUE INDEX IF NOT EXISTS idx_profiles_telegram_chat
  ON profiles(telegram_chat_id) WHERE telegram_chat_id IS NOT NULL;

-- The delivery row records which chat actually received it, kept separate from
-- profiles.telegram_chat_id so a later re-link does not rewrite history: a
-- failure has to be diagnosable against the chat that was actually used.
ALTER TABLE message_deliveries ADD COLUMN IF NOT EXISTS telegram_chat_id bigint;

-- Telegram acknowledges with its own message_id; provider_message_id already
-- exists and carries it.

COMMENT ON COLUMN profiles.telegram_chat_id IS
  'Bound only after the patient opens the bot via a t.me deep link. Cannot be derived from a phone number.';

COMMIT;

-- ============================================================================
-- 20260902000002_rls_hardening.sql
-- ============================================================================
-- Assessment §1.1, §2.5: tenant defence-in-depth, and interaction_log forgery.
--
-- Idempotent.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. RESTRICTIVE tenant policies on the five tables that lacked one
-- ---------------------------------------------------------------------------
-- Their permissive policies ARE self- or clinic-scoped, so this is not closing an
-- open door — it is adding the second barrier the other six tables already have.
-- The failure it guards against is a future edit to a permissive policy: with
-- only one barrier that is a cross-tenant leak, with two it is a bug that denies.
--
-- RESTRICTIVE is ANDed with the permissive set, so it can only ever narrow.
-- Adding these as PERMISSIVE would have WIDENED access — the opposite of intent —
-- because Postgres ORs permissive policies together.

DROP POLICY IF EXISTS "Tenant isolation" ON profiles;
DROP POLICY IF EXISTS "Tenant isolation" ON interaction_log;
DROP POLICY IF EXISTS "Tenant isolation" ON patient_access_tokens;
DROP POLICY IF EXISTS "Tenant isolation" ON message_deliveries;

CREATE POLICY "Tenant isolation" ON profiles              AS RESTRICTIVE FOR ALL USING (clinic_id = get_user_clinic_id());
CREATE POLICY "Tenant isolation" ON patient_access_tokens AS RESTRICTIVE FOR ALL USING (clinic_id = get_user_clinic_id());
CREATE POLICY "Tenant isolation" ON message_deliveries    AS RESTRICTIVE FOR ALL USING (clinic_id = get_user_clinic_id());

-- interaction_log has no clinic_id column; it is scoped through the acting user.
-- Comparing the row's user to the caller's clinic keeps the same guarantee
-- without a schema change on a table that is append-only in practice.
CREATE POLICY "Tenant isolation" ON interaction_log AS RESTRICTIVE FOR ALL
  USING (
    user_id IN (SELECT id FROM public.profiles WHERE clinic_id = get_user_clinic_id())
  );

-- `clinics` is deliberately NOT given one. Its only policy is already
-- `USING (id = get_user_clinic_id())` — the row IS the tenant, so a tenant
-- predicate would restate the same comparison. Adding one would be noise that
-- implies a protection it does not add.

-- ---------------------------------------------------------------------------
-- 2. interaction_log: stop the client choosing what gets recorded
-- ---------------------------------------------------------------------------
-- The INSERT policy is WITH CHECK (user_id = auth.uid()) and nothing else, so a
-- caller could set action_type, target_metadata and created_at freely. Those rows
-- feed services/importance.py, which means a non-clinician could push the ranking
-- toward or away from chosen topics. The absolute floor still prevents an allergy
-- being buried, so the damage was bounded to noise — but the loop should not be
-- writable by its subjects at all.
--
-- Server-side authority for the fields that carry meaning:
CREATE OR REPLACE FUNCTION stamp_interaction_log()
RETURNS trigger AS $$
DECLARE
  v_role text;
BEGIN
  IF current_user NOT IN ('authenticated', 'anon') THEN
    RETURN NEW;   -- service-role ingestion and seeding keep full control
  END IF;

  -- Identity and time are asserted by the server, never accepted from the body.
  NEW.user_id    := auth.uid();
  NEW.created_at := now();

  SELECT role INTO v_role FROM public.profiles WHERE id = auth.uid();
  NEW.user_role := COALESCE(v_role, 'unknown');

  -- The learning loop reads target_metadata. Free-form client JSON there is the
  -- forgery surface, so it is reduced to the keys the scorer actually uses.
  -- Anything else a client sends is discarded rather than stored and trusted.
  IF NEW.target_metadata IS NOT NULL THEN
    NEW.target_metadata := jsonb_strip_nulls(jsonb_build_object(
      'keywords',   NEW.target_metadata -> 'keywords',
      'topic',      NEW.target_metadata -> 'topic',
      'risk_level', NEW.target_metadata -> 'risk_level'
    ));
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

DROP TRIGGER IF EXISTS trg_stamp_interaction_log ON interaction_log;
CREATE TRIGGER trg_stamp_interaction_log
  BEFORE INSERT ON interaction_log
  FOR EACH ROW EXECUTE FUNCTION stamp_interaction_log();

COMMIT;

