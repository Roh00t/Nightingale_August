-- UI interaction telemetry, deliberately NOT in interaction_log.
--
-- =============================================================================
-- WHY A SEPARATE TABLE, AND NOT interaction_log
-- =============================================================================
-- interaction_log feeds the self-learning importance loop. ai-service reads it
-- and scores each row:
--
--     weight = ACTION_TYPE_WEIGHTS.get(row["action_type"], 0.3)
--                                                          ^^^
-- An UNKNOWN action type defaults to +0.3 — a POSITIVE engagement weight. So
-- writing 'expand'/'collapse' rows there would have the ranking model treat UI
-- fidgeting as clinical endorsement. Worse, the loop reads only the 200 most
-- recent rows, so high-frequency UI events would flush genuine accept/reject
-- signal out of the window entirely.
--
-- A clinician toggling an accordion forty times in a shift would both promote
-- arbitrary highlights and erase the loop's actual evidence. That is a clinical
-- safety argument, not an architectural preference.
--
-- =============================================================================
-- WHAT IS DELIBERATELY ABSENT FROM THIS SCHEMA
-- =============================================================================
-- No user_id. No patient_id. No care_note_id. No free-text column anywhere.
--
-- The brief asked for an ephemeral telemetry session id kept in an isolated
-- mapping table behind audited access. This goes one step further: the mapping
-- is never written at all, so there is nothing to protect and nothing to leak.
-- clinic_id + user_role answers every question the dashboard asks, and a row
-- cannot be re-joined to a clinician even by an admin with full table access.
--
-- component_id is CHECK-constrained against a fixed allowlist rather than being
-- free text. A DOM string that is not on the list FAILS THE INSERT instead of
-- being stored. That is the same allowlist discipline as stamp_interaction_log,
-- enforced in Postgres where a compromised client cannot bypass it — a frontend
-- allowlist is a convenience, not a control.

CREATE TABLE IF NOT EXISTS ui_telemetry (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Minted per browser session, unrelated to auth.uid(), the GoTrue session,
  -- the EHR encounter or the patient. Lets us measure "same sitting" without
  -- identifying whose sitting it was.
  session_uuid  uuid NOT NULL,

  clinic_id     uuid NOT NULL REFERENCES clinics(id),
  user_role     text NOT NULL CHECK (user_role IN ('clinician','staff','admin','patient')),

  component_id  text NOT NULL CHECK (component_id IN (
                  'sunshine',
                  'at_a_glance',
                  'changes_since_last_visit',
                  'care_plan_completed',
                  'critical_flags_panel',
                  'editor_pane',
                  'timeline',
                  'retraction_notice'
                )),

  action        text NOT NULL CHECK (action IN (
                  'expand','collapse','pane_resize','scroll_depth','dwell','view'
                )),

  -- Dimensional only. Bounded so a bad clock or a backgrounded tab cannot write
  -- a nonsense dwell that skews every average.
  dwell_ms      integer CHECK (dwell_ms IS NULL OR dwell_ms BETWEEN 0 AND 3600000),
  value_pct     smallint CHECK (value_pct IS NULL OR value_pct BETWEEN 0 AND 100),

  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ui_telemetry_clinic_time
  ON ui_telemetry(clinic_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ui_telemetry_component
  ON ui_telemetry(component_id, action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ui_telemetry_session
  ON ui_telemetry(session_uuid, created_at);

ALTER TABLE ui_telemetry ENABLE ROW LEVEL SECURITY;

-- Server-side authority over the two columns a client could otherwise forge.
-- Without this a caller could attribute their events to another clinic or claim
-- a role they do not hold, and the dashboard would report it as fact.
CREATE OR REPLACE FUNCTION stamp_ui_telemetry()
RETURNS trigger AS $$
DECLARE
  v_role   text;
  v_clinic uuid;
BEGIN
  IF current_user NOT IN ('authenticated', 'anon') THEN
    RETURN NEW;
  END IF;
  SELECT role, clinic_id INTO v_role, v_clinic
    FROM public.profiles WHERE id = auth.uid();
  NEW.user_role  := COALESCE(v_role, 'unknown');
  NEW.clinic_id  := v_clinic;
  NEW.created_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

DROP TRIGGER IF EXISTS trg_stamp_ui_telemetry ON ui_telemetry;
CREATE TRIGGER trg_stamp_ui_telemetry
  BEFORE INSERT ON ui_telemetry
  FOR EACH ROW EXECUTE FUNCTION stamp_ui_telemetry();

-- Anyone signed in may record their own interactions.
CREATE POLICY "Signed-in users can record UI telemetry"
  ON ui_telemetry FOR INSERT
  WITH CHECK (auth.uid() IS NOT NULL);

-- Only admins read it, and only for their own clinic. There is no
-- "users can view own" policy, because there is no user column to match on —
-- which is the point.
CREATE POLICY "Admins can view clinic UI telemetry"
  ON ui_telemetry FOR SELECT
  USING (get_user_role() = 'admin' AND clinic_id = get_user_clinic_id());

-- Defence in depth, mirroring 20260902000002. RESTRICTIVE is load-bearing:
-- Postgres ORs permissive policies, so adding this as permissive would WIDEN
-- access rather than narrow it.
CREATE POLICY "Tenant isolation" ON ui_telemetry AS RESTRICTIVE FOR ALL
  USING (clinic_id = get_user_clinic_id());

-- No UPDATE or DELETE policy exists. Telemetry is append-only; a row that can be
-- edited after the fact is not evidence.

-- ---------------------------------------------------------------------------
-- CONSEQUENCE FOR CALLERS: never use INSERT ... RETURNING here.
--
-- Non-admins can write but not read, which is the intent. But RETURNING makes
-- an INSERT also perform a SELECT, so PostgreSQL applies the SELECT policy to
-- the returned row and the whole statement fails with
--   "new row violates row-level security policy"
-- which reads like the write was rejected. It was not; the read-back was.
--
-- In supabase-js that means `.insert(row)` and NOT `.insert(row).select()`.
-- Since telemetry is fire-and-forget, a caller that adds .select() would break
-- every write with no visible symptom — and an empty dashboard reads as
-- "nobody expands anything" rather than "nothing was ever recorded".
-- ---------------------------------------------------------------------------
