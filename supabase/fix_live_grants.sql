-- Apply to an EXISTING deployment that already has 001_foundation.sql.
--
-- 001 previously created the tables without granting privileges to Supabase's
-- roles, so PostgREST answers every request with
--   42501  permission denied for table <name>
-- even for service_role. RLS is unaffected: it still decides which rows each
-- caller sees. This only opens the table-level door that RLS then guards.
--
-- Idempotent. Safe to run more than once.
-- Run in: Supabase Dashboard -> SQL Editor -> New query -> Run.

GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO authenticated;

GRANT ALL ON ALL TABLES    IN SCHEMA public TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;

GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO authenticated, service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL ON TABLES TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT EXECUTE ON FUNCTIONS TO authenticated, service_role;

-- seed_demo_data writes to every table; running it as owner means it does not
-- depend on the caller's grants.
ALTER FUNCTION seed_demo_data(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid) SECURITY DEFINER;

-- ---------------------------------------------------------------------------
-- Clinical safety columns on highlights (additive; safe on a live database).
-- importance / confidence / risk are three different quantities and are stored
-- separately so the UI cannot render one as another.
-- ---------------------------------------------------------------------------
ALTER TABLE highlights ADD COLUMN IF NOT EXISTS confidence_score float;
ALTER TABLE highlights ADD COLUMN IF NOT EXISTS confidence_band  text;
ALTER TABLE highlights ADD COLUMN IF NOT EXISTS risk_floor       text;
ALTER TABLE highlights ADD COLUMN IF NOT EXISTS model_risk       text;
ALTER TABLE highlights ADD COLUMN IF NOT EXISTS abstained        boolean NOT NULL DEFAULT false;
ALTER TABLE highlights ADD COLUMN IF NOT EXISTS safety_metadata  jsonb DEFAULT '{}';

DO $$ BEGIN
  ALTER TABLE highlights ADD CONSTRAINT highlights_confidence_score_check
    CHECK (confidence_score IS NULL OR (confidence_score >= 0.0 AND confidence_score <= 1.0));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE highlights ADD CONSTRAINT highlights_confidence_band_check
    CHECK (confidence_band IS NULL OR confidence_band IN ('high','medium','low'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_highlights_surfaced
  ON highlights(care_note_id, abstained, importance_score DESC);

-- Verify: should return 8 rows, one per table.
SELECT table_name,
       has_table_privilege('authenticated', 'public.' || table_name, 'SELECT') AS authenticated_select,
       has_table_privilege('service_role',  'public.' || table_name, 'SELECT') AS service_role_select
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name;

-- ---------------------------------------------------------------------------
-- PATIENT DATA ISOLATION — move the clinical risk assessment out of a
-- patient-readable row.
--
-- care_notes.glance_cache has to be readable by the patient who owns the row;
-- it carries their care-plan progress. But RLS is ROW-level, not COLUMN-level,
-- so every other key in that jsonb was readable too. A patient calling
-- PostgREST directly with their own JWT could read the clinician's internal
-- assessment verbatim:
--
--   {"text": "eGFR declining: 62 -> 45 over 6 months", "confidence": 0.92,
--    "risk": "critical"}
--   {"text": "Cardiology referral pending since Jan 15", "status": "unresolved"}
--
-- Stripping those fields in a server component only hid them from the page.
-- The fix is structural: the assessment moves to its own table with no patient
-- policy, so Postgres returns zero rows to a patient by whatever route.
--
-- Idempotent, and safe to run on a database that already holds real rows.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS care_note_assessments (
  care_note_id  uuid PRIMARY KEY REFERENCES care_notes(id) ON DELETE CASCADE,
  assessment    jsonb NOT NULL DEFAULT '{}',
  updated_at    timestamptz DEFAULT now()
);

ALTER TABLE care_note_assessments ENABLE ROW LEVEL SECURITY;

-- Dropped first so re-running this file cannot leave two definitions of the
-- same policy behind — the failure mode that made 007/012/014 unreadable.
DROP POLICY IF EXISTS "Care team can view assessments"   ON care_note_assessments;
DROP POLICY IF EXISTS "Clinicians can write assessments" ON care_note_assessments;
DROP POLICY IF EXISTS "Clinicians can update assessments" ON care_note_assessments;

-- No patient policy, deliberately. There is no rule that could admit a patient.
CREATE POLICY "Care team can view assessments"
  ON care_note_assessments FOR SELECT
  USING (
    check_care_note_access(care_note_id)
    AND get_user_role() IN ('staff', 'clinician', 'admin')
  );

CREATE POLICY "Clinicians can write assessments"
  ON care_note_assessments FOR INSERT
  WITH CHECK (
    check_care_note_access(care_note_id)
    AND get_user_role() IN ('clinician', 'admin')
  );

CREATE POLICY "Clinicians can update assessments"
  ON care_note_assessments FOR UPDATE
  USING (
    check_care_note_access(care_note_id)
    AND get_user_role() IN ('clinician', 'admin')
  );

GRANT SELECT, INSERT, UPDATE, DELETE ON care_note_assessments TO authenticated;
GRANT ALL                            ON care_note_assessments TO service_role;

-- Backfill: carry any assessment already sitting in glance_cache across.
-- ON CONFLICT DO NOTHING so a second run does not overwrite edits made since.
INSERT INTO care_note_assessments (care_note_id, assessment)
SELECT
  id,
  jsonb_strip_nulls(jsonb_build_object(
    'top_items',                glance_cache -> 'top_items',
    'changes_since_last_visit', glance_cache -> 'changes_since_last_visit'
  ))
FROM care_notes
WHERE glance_cache ? 'top_items'
   OR glance_cache ? 'changes_since_last_visit'
ON CONFLICT (care_note_id) DO NOTHING;

-- Then remove them from the patient-readable row. This is the step that
-- actually closes the leak; everything above only prepares somewhere to put
-- the data. Run it last so a failure cannot destroy the assessment.
UPDATE care_notes
SET glance_cache = glance_cache - 'top_items' - 'changes_since_last_visit'
WHERE glance_cache ? 'top_items'
   OR glance_cache ? 'changes_since_last_visit';

-- ---------------------------------------------------------------------------
-- PATIENT-FACING WRITES MUST CLEAR THE MAKER-CHECKER GATE
--
-- Anything a patient reads has to pass grounding and prohibited-speech checks
-- first. Those run in the AI service, which files the approved entry with the
-- service-role key and therefore bypasses RLS. Restricting user JWTs to
-- `visibility = 'internal'` is what stops the gate being skipped by simply not
-- calling it — a clinician's own token in curl now writes nothing patient-facing.
--
-- Idempotent.
-- ---------------------------------------------------------------------------

DROP POLICY IF EXISTS "Clinicians can create any entry"   ON timeline_entries;
DROP POLICY IF EXISTS "Staff can create staff entries"    ON timeline_entries;

CREATE POLICY "Clinicians can create any entry"
  ON timeline_entries FOR INSERT
  WITH CHECK (
    check_care_note_access(care_note_id)
    AND get_user_role() IN ('clinician', 'admin')
    AND author_id = auth.uid()
    AND visibility = 'internal'
  );

CREATE POLICY "Staff can create staff entries"
  ON timeline_entries FOR INSERT
  WITH CHECK (
    check_care_note_access(care_note_id)
    AND get_user_role() = 'staff'
    AND author_role = 'staff'
    AND author_id = auth.uid()
    AND visibility = 'internal'
  );
