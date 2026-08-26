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

-- Verify: should return 8 rows, one per table.
SELECT table_name,
       has_table_privilege('authenticated', 'public.' || table_name, 'SELECT') AS authenticated_select,
       has_table_privilege('service_role',  'public.' || table_name, 'SELECT') AS service_role_select
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name;
