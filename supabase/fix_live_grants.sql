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
