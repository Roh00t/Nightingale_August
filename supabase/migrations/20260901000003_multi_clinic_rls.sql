-- Multi-clinic isolation, defence in depth.
--
-- READ THIS BEFORE CHANGING THE SCOPING MECHANISM.
--
-- The audit asked for policies of the form
--     auth.jwt() ->> 'clinic_id' = clinic_id
-- and this migration deliberately does NOT do that. Two reasons, both of which
-- are failure modes rather than preferences:
--
--   1. Supabase access tokens do not carry `clinic_id` unless a custom access
--      token hook adds it. Absent the claim, `auth.jwt() ->> 'clinic_id'` is
--      NULL, and `NULL = clinic_id` is NULL, which is not TRUE — so every
--      policy denies and the entire application locks out. That failure is at
--      least loud. The quiet version is worse: someone "fixes" the lockout with
--      COALESCE or an OR, and the isolation silently disappears.
--
--   2. A JWT claim is a snapshot. It refreshes on token refresh, not on change.
--      Move a clinician between clinics, or revoke their access, and their
--      existing token keeps the old clinic_id until it expires — they retain
--      read access to a clinic they have been removed from for the remainder of
--      the token lifetime. `profiles.clinic_id` is authoritative and takes
--      effect on the next query.
--
-- So clinic scoping stays on `get_user_clinic_id()`, a SECURITY DEFINER read of
-- profiles (001_foundation.sql). What this migration adds is the part the audit
-- is actually reaching for: **isolation that holds at the row even if a policy
-- helper is wrong**, by denormalising clinic_id onto every patient-scoped table
-- so each one can be checked directly instead of only through a join.
--
-- Before: timeline_entries is scoped by check_care_note_access(care_note_id),
-- which resolves the clinic through care_notes. Correct, but single-path — a
-- bug in that helper is a cross-tenant read on six tables at once.
-- After: each table also carries its own clinic_id, filled and locked by
-- trigger, and the policy checks both. Two independent derivations of the same
-- fact must agree.
--
-- Idempotent. Safe on a database that already holds rows.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Denormalised clinic_id on every patient-scoped table
-- ---------------------------------------------------------------------------
-- Nullable at first so the backfill can run; NOT NULL is applied afterwards.
-- Adding it NOT NULL up front fails on any table that already has rows.

ALTER TABLE timeline_entries      ADD COLUMN IF NOT EXISTS clinic_id uuid REFERENCES clinics(id);
ALTER TABLE note_versions         ADD COLUMN IF NOT EXISTS clinic_id uuid REFERENCES clinics(id);
ALTER TABLE comments              ADD COLUMN IF NOT EXISTS clinic_id uuid REFERENCES clinics(id);
ALTER TABLE highlights            ADD COLUMN IF NOT EXISTS clinic_id uuid REFERENCES clinics(id);
ALTER TABLE care_note_assessments ADD COLUMN IF NOT EXISTS clinic_id uuid REFERENCES clinics(id);

UPDATE timeline_entries      t SET clinic_id = c.clinic_id FROM care_notes c WHERE c.id = t.care_note_id AND t.clinic_id IS NULL;
UPDATE note_versions         v SET clinic_id = c.clinic_id FROM care_notes c WHERE c.id = v.care_note_id AND v.clinic_id IS NULL;
UPDATE comments              m SET clinic_id = c.clinic_id FROM care_notes c WHERE c.id = m.care_note_id AND m.clinic_id IS NULL;
UPDATE highlights            h SET clinic_id = c.clinic_id FROM care_notes c WHERE c.id = h.care_note_id AND h.clinic_id IS NULL;
UPDATE care_note_assessments a SET clinic_id = c.clinic_id FROM care_notes c WHERE c.id = a.care_note_id AND a.clinic_id IS NULL;

ALTER TABLE timeline_entries      ALTER COLUMN clinic_id SET NOT NULL;
ALTER TABLE note_versions         ALTER COLUMN clinic_id SET NOT NULL;
ALTER TABLE comments              ALTER COLUMN clinic_id SET NOT NULL;
ALTER TABLE highlights            ALTER COLUMN clinic_id SET NOT NULL;
ALTER TABLE care_note_assessments ALTER COLUMN clinic_id SET NOT NULL;

-- ---------------------------------------------------------------------------
-- 2. The trigger that makes the denormalisation trustworthy
-- ---------------------------------------------------------------------------
-- A denormalised column that callers set themselves is worse than no column:
-- it looks like an independent check while actually being caller-controlled, so
-- an attacker supplies their own clinic_id and passes both halves of the policy.
--
-- This derives it from the parent row on every INSERT and UPDATE, ignoring
-- whatever the caller sent. The column therefore cannot drift from care_notes
-- and cannot be forged.

CREATE OR REPLACE FUNCTION set_clinic_id_from_care_note()
RETURNS trigger AS $$
BEGIN
  SELECT clinic_id INTO NEW.clinic_id
  FROM public.care_notes
  WHERE id = NEW.care_note_id;

  IF NEW.clinic_id IS NULL THEN
    RAISE EXCEPTION 'care_note % does not exist; refusing to write an unscoped row', NEW.care_note_id;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

DROP TRIGGER IF EXISTS trg_clinic_id ON timeline_entries;
DROP TRIGGER IF EXISTS trg_clinic_id ON note_versions;
DROP TRIGGER IF EXISTS trg_clinic_id ON comments;
DROP TRIGGER IF EXISTS trg_clinic_id ON highlights;
DROP TRIGGER IF EXISTS trg_clinic_id ON care_note_assessments;

CREATE TRIGGER trg_clinic_id BEFORE INSERT OR UPDATE ON timeline_entries
  FOR EACH ROW EXECUTE FUNCTION set_clinic_id_from_care_note();
CREATE TRIGGER trg_clinic_id BEFORE INSERT OR UPDATE ON note_versions
  FOR EACH ROW EXECUTE FUNCTION set_clinic_id_from_care_note();
CREATE TRIGGER trg_clinic_id BEFORE INSERT OR UPDATE ON comments
  FOR EACH ROW EXECUTE FUNCTION set_clinic_id_from_care_note();
CREATE TRIGGER trg_clinic_id BEFORE INSERT OR UPDATE ON highlights
  FOR EACH ROW EXECUTE FUNCTION set_clinic_id_from_care_note();
CREATE TRIGGER trg_clinic_id BEFORE INSERT OR UPDATE ON care_note_assessments
  FOR EACH ROW EXECUTE FUNCTION set_clinic_id_from_care_note();

-- ---------------------------------------------------------------------------
-- 3. A second, independent tenant check on every patient-scoped table
-- ---------------------------------------------------------------------------
-- RESTRICTIVE, not PERMISSIVE. Postgres ORs permissive policies together, so
-- adding another permissive one would WIDEN access — the opposite of the intent.
-- A restrictive policy is ANDed with whatever else applies, so this can only
-- ever narrow. Every existing policy in 001_foundation.sql keeps working and
-- now additionally has to satisfy the tenant match.
--
-- This is the property the audit asked for: if a route handler forgets to
-- filter, or check_care_note_access() is subtly wrong, the row still does not
-- cross a clinic boundary.

DROP POLICY IF EXISTS "Tenant isolation" ON timeline_entries;
DROP POLICY IF EXISTS "Tenant isolation" ON note_versions;
DROP POLICY IF EXISTS "Tenant isolation" ON comments;
DROP POLICY IF EXISTS "Tenant isolation" ON highlights;
DROP POLICY IF EXISTS "Tenant isolation" ON care_note_assessments;
DROP POLICY IF EXISTS "Tenant isolation" ON care_notes;

CREATE POLICY "Tenant isolation" ON timeline_entries      AS RESTRICTIVE FOR ALL USING (clinic_id = get_user_clinic_id());
CREATE POLICY "Tenant isolation" ON note_versions         AS RESTRICTIVE FOR ALL USING (clinic_id = get_user_clinic_id());
CREATE POLICY "Tenant isolation" ON comments              AS RESTRICTIVE FOR ALL USING (clinic_id = get_user_clinic_id());
CREATE POLICY "Tenant isolation" ON highlights            AS RESTRICTIVE FOR ALL USING (clinic_id = get_user_clinic_id());
CREATE POLICY "Tenant isolation" ON care_note_assessments AS RESTRICTIVE FOR ALL USING (clinic_id = get_user_clinic_id());
CREATE POLICY "Tenant isolation" ON care_notes            AS RESTRICTIVE FOR ALL USING (clinic_id = get_user_clinic_id());

-- Indexes: the restrictive policy adds clinic_id to the predicate of every
-- query against these tables, so it belongs in the composite lead alongside the
-- existing care_note_id indexes.
CREATE INDEX IF NOT EXISTS idx_timeline_clinic  ON timeline_entries(clinic_id, care_note_id);
CREATE INDEX IF NOT EXISTS idx_versions_clinic  ON note_versions(clinic_id, care_note_id);
CREATE INDEX IF NOT EXISTS idx_comments_clinic  ON comments(clinic_id, care_note_id);
CREATE INDEX IF NOT EXISTS idx_highlights_clinic ON highlights(clinic_id, care_note_id);

COMMIT;
