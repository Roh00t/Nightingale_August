-- Grant + RLS posture for every table in `public`, enumerated from the catalog.
--
-- Enumerated, not listed by hand: a fixed IN (...) list silently omits tables
-- added later, and reads as a clean result while the new table goes unchecked.
-- care_note_assessments was added after the first grants pass and is exactly the
-- case that would slip through.
--
-- Run in: Supabase Dashboard -> SQL Editor.
--
-- Expected: every row `true, true, true`. A false in authenticated_select means
-- PostgREST answers 42501 for that table whatever RLS says.
--
-- Note what this does NOT tell you. A grant is the table-level door; RLS decides
-- rows. `authenticated` holding SELECT on care_note_assessments is correct — the
-- patient still gets zero rows there, because no policy admits them. Grants and
-- row visibility are separate questions, and only the API probe in
-- scripts/verify_patient_isolation.mjs answers the second one.

SELECT
  c.relname                                                   AS table_name,
  has_table_privilege('authenticated', c.oid, 'SELECT')       AS authenticated_select,
  has_table_privilege('authenticated', c.oid, 'INSERT')       AS authenticated_insert,
  has_table_privilege('service_role',  c.oid, 'SELECT')       AS service_role_select,
  c.relrowsecurity                                            AS rls_enabled,
  (SELECT count(*) FROM pg_policies p
    WHERE p.schemaname = 'public' AND p.tablename = c.relname) AS policies
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind = 'r'
ORDER BY c.relname;
