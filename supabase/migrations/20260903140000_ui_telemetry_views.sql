-- Four views, one per question in the telemetry brief. Not a product.
--
-- Each is scoped through ui_telemetry, so the RLS on that table applies: an
-- admin sees their own clinic and nobody else sees anything. `security_invoker`
-- is what makes that true — without it a view runs as its owner and would hand
-- every clinic's data to any reader, which is the classic way a "read-only
-- dashboard" becomes a cross-tenant leak.

-- 1. TIME-TO-EXPANSION. How long after the page renders does a clinician open
--    each collapsed section? Consistently low means default-closed is a tax,
--    not a help, and that section should be open by default.
CREATE OR REPLACE VIEW v_time_to_expansion
WITH (security_invoker = true) AS
SELECT
  component_id,
  user_role,
  count(*)                                             AS expansions,
  round(avg(dwell_ms) / 1000.0, 1)                     AS avg_seconds_to_open,
  round((percentile_cont(0.5) WITHIN GROUP (ORDER BY dwell_ms))::numeric / 1000.0, 1)
                                                       AS median_seconds_to_open,
  -- The decision line. Under ~3s means they went straight for it.
  count(*) FILTER (WHERE dwell_ms < 3000)              AS opened_within_3s
FROM ui_telemetry
WHERE action = 'expand' AND dwell_ms IS NOT NULL
GROUP BY component_id, user_role;

-- 2. ORPHANED EXPANSIONS. Opened, glanced at for under a second, closed with no
--    interaction. They were hunting for one value that belongs higher up.
CREATE OR REPLACE VIEW v_orphaned_expansions
WITH (security_invoker = true) AS
SELECT
  component_id,
  count(*)                                             AS quick_closes,
  round(avg(dwell_ms))                                 AS avg_ms_open
FROM ui_telemetry
WHERE action = 'collapse' AND dwell_ms IS NOT NULL AND dwell_ms < 1000
GROUP BY component_id;

-- 3. TOGGLE OSCILLATION. The replacement for "tab thrashing" — there are no
--    tabs, so the equivalent signal is a section opened and closed repeatedly
--    inside one sitting. High counts mean the content should not be behind a
--    disclosure at all.
CREATE OR REPLACE VIEW v_toggle_oscillation
WITH (security_invoker = true) AS
SELECT
  component_id,
  session_uuid,
  count(*) FILTER (WHERE action = 'expand')            AS opens,
  count(*) FILTER (WHERE action = 'collapse')          AS closes
FROM ui_telemetry
WHERE action IN ('expand','collapse')
GROUP BY component_id, session_uuid
HAVING count(*) FILTER (WHERE action = 'expand') >= 3;

-- 4. BLIND SESSIONS. Sittings where a section was never opened at all. The
--    honest reading is ambiguous — either the summary line was enough, or the
--    content is being ignored because of the extra click — so this reports the
--    ratio and does not claim which. It is the number to watch, not to act on
--    alone.
CREATE OR REPLACE VIEW v_blind_sessions
WITH (security_invoker = true) AS
WITH sessions AS (SELECT DISTINCT session_uuid FROM ui_telemetry),
     opened   AS (SELECT DISTINCT session_uuid FROM ui_telemetry WHERE action = 'expand')
SELECT
  (SELECT count(*) FROM sessions)                                   AS total_sessions,
  (SELECT count(*) FROM opened)                                     AS sessions_with_an_expand,
  (SELECT count(*) FROM sessions) - (SELECT count(*) FROM opened)   AS sessions_with_none,
  CASE WHEN (SELECT count(*) FROM sessions) = 0 THEN NULL
       ELSE round(100.0 * ((SELECT count(*) FROM sessions) - (SELECT count(*) FROM opened))
                  / (SELECT count(*) FROM sessions), 1)
  END                                                               AS pct_never_expanded;

COMMENT ON VIEW v_time_to_expansion IS
  'Q1: is default-closed costing time? Low seconds_to_open = the section should be open.';
COMMENT ON VIEW v_orphaned_expansions IS
  'Q2: opened and shut within a second = hunting for a value that belongs higher up.';
COMMENT ON VIEW v_toggle_oscillation IS
  'Q3: repeated open/close in one sitting = should not be behind a disclosure.';
COMMENT ON VIEW v_blind_sessions IS
  'Q4: sittings that never expanded anything. Ambiguous by construction — watch, do not act alone.';
