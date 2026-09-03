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
