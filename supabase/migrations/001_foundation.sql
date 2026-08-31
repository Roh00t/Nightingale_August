-- =============================================================================
-- Nightingale — Foundation
-- =============================================================================
-- Squash of the historical 001-014 chain into a single expression of the
-- INTENDED FINAL STATE. Predecessors are kept for reference in
-- supabase/migrations_archive/.
--
-- Why the squash: the old chain repeatedly reverted itself. 014 dropped the
-- policies 006/007 created and reinstated the nested-EXISTS pattern they were
-- written to eliminate; 014 also silently dropped 012's patient-readback
-- clause. 013 re-hardcoded the care_plan_score that 008 had just repaired, and
-- redefined seed_demo_data at a new arity so the two-clinic version became
-- permanently unreachable.
--
-- Rules this file holds to (guardrails.md §3, §4):
--   M1  one definition per policy, here and nowhere else
--   R1  no nested EXISTS on an RLS-protected table inside a policy
--   R2  every SECURITY DEFINER function sets search_path = public
--   R3  access helpers encode tenant AND role
--   R4  patient policies scope on ownership, never on clinic
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================================
-- 1. Types
-- =============================================================================

-- profiles.role is a true enum (Phase 1 mandate). The other role-ish columns
-- stay as text + CHECK on purpose: timeline_entries.author_role admits 'system'
-- while profiles.role must not, and entry_type has already needed one widening
-- (historical 009). Separate enums for those would couple unrelated churn.
CREATE TYPE user_role AS ENUM ('patient', 'staff', 'clinician', 'admin');

-- =============================================================================
-- 2. Tables
-- =============================================================================

CREATE TABLE clinics (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL,
  settings    jsonb DEFAULT '{}',
  created_at  timestamptz DEFAULT now()
);

CREATE TABLE profiles (
  id           uuid PRIMARY KEY REFERENCES auth.users ON DELETE CASCADE,
  clinic_id    uuid NOT NULL REFERENCES clinics(id),
  role         user_role NOT NULL,
  display_name text NOT NULL,
  avatar_url   text,
  created_at   timestamptz DEFAULT now()
);

CREATE INDEX idx_profiles_clinic ON profiles(clinic_id);
CREATE INDEX idx_profiles_role   ON profiles(clinic_id, role);

-- One longitudinal note per patient.
CREATE TABLE care_notes (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id               uuid NOT NULL UNIQUE REFERENCES profiles(id),
  clinic_id                uuid NOT NULL REFERENCES clinics(id),
  yjs_state                bytea,
  glance_cache             jsonb DEFAULT '{}',
  glance_cache_updated_at  timestamptz DEFAULT now(),
  created_at               timestamptz DEFAULT now(),
  updated_at               timestamptz DEFAULT now()
);

CREATE INDEX idx_care_notes_patient ON care_notes(patient_id);
CREATE INDEX idx_care_notes_clinic  ON care_notes(clinic_id);

-- author_id carries a single FK to profiles, named to match the PostgREST join
-- hint the frontend already uses (profiles!timeline_entries_author_profile_fkey).
-- profiles.id itself references auth.users, so the auth linkage is transitive.
CREATE TABLE timeline_entries (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  care_note_id        uuid NOT NULL REFERENCES care_notes(id) ON DELETE CASCADE,
  author_role         text NOT NULL CHECK (author_role IN ('patient','staff','clinician','admin','system')),
  author_id           uuid CONSTRAINT timeline_entries_author_profile_fkey REFERENCES profiles(id),
  entry_type          text NOT NULL CHECK (entry_type IN (
                        'manual_note', 'ai_doctor_consult_summary', 'ai_nurse_consult_summary',
                        'ai_patient_session_summary', 'instruction', 'admin', 'system_event',
                        'patient_message'
                      )),
  content             jsonb NOT NULL DEFAULT '{}',
  content_text        text,
  provenance_pointer  jsonb,
  risk_level          text NOT NULL DEFAULT 'info' CHECK (risk_level IN ('critical','high','medium','low','info')),
  visibility          text NOT NULL DEFAULT 'internal' CHECK (visibility IN ('internal','patient_visible')),
  metadata            jsonb DEFAULT '{}',
  is_archived         boolean NOT NULL DEFAULT false,
  created_at          timestamptz DEFAULT now(),
  updated_at          timestamptz DEFAULT now()
);

CREATE INDEX idx_timeline_entries_care_note ON timeline_entries(care_note_id, created_at DESC);
CREATE INDEX idx_timeline_entries_type      ON timeline_entries(care_note_id, entry_type);
CREATE INDEX idx_timeline_entries_risk      ON timeline_entries(care_note_id, risk_level);
CREATE INDEX idx_timeline_entries_archived  ON timeline_entries(care_note_id, is_archived, created_at DESC);

CREATE TABLE note_versions (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  care_note_id      uuid NOT NULL REFERENCES care_notes(id) ON DELETE CASCADE,
  version_number    integer NOT NULL,
  yjs_snapshot      bytea,
  content_snapshot  jsonb,
  changed_by        uuid REFERENCES profiles(id),
  change_summary    text,
  created_at        timestamptz DEFAULT now(),
  UNIQUE (care_note_id, version_number)
);

CREATE INDEX idx_note_versions_care_note ON note_versions(care_note_id, version_number DESC);

CREATE TABLE comments (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  care_note_id       uuid NOT NULL REFERENCES care_notes(id) ON DELETE CASCADE,
  timeline_entry_id  uuid REFERENCES timeline_entries(id) ON DELETE CASCADE,
  parent_comment_id  uuid REFERENCES comments(id) ON DELETE CASCADE,
  author_id          uuid NOT NULL CONSTRAINT comments_author_profile_fkey REFERENCES profiles(id),
  author_role        text NOT NULL CHECK (author_role IN ('patient','staff','clinician','admin')),
  content            text NOT NULL,
  anchor_data        jsonb,
  is_resolved        boolean DEFAULT false,
  resolved_by        uuid REFERENCES profiles(id),
  mentions           uuid[] DEFAULT '{}',
  created_at         timestamptz DEFAULT now()
);

CREATE INDEX idx_comments_care_note ON comments(care_note_id, created_at DESC);
CREATE INDEX idx_comments_entry     ON comments(timeline_entry_id);
CREATE INDEX idx_comments_parent    ON comments(parent_comment_id);

CREATE TABLE highlights (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  care_note_id        uuid NOT NULL REFERENCES care_notes(id) ON DELETE CASCADE,
  source_entry_id     uuid REFERENCES timeline_entries(id) ON DELETE SET NULL,
  content_snippet     text NOT NULL,
  risk_reason         text NOT NULL,
  risk_level          text NOT NULL CHECK (risk_level IN ('critical','high','medium','low','info')),
  importance_score    float NOT NULL DEFAULT 0.5 CHECK (importance_score >= 0.0 AND importance_score <= 1.0),
  provenance_pointer  jsonb,
  is_accepted         boolean,
  is_pinned           boolean DEFAULT false,
  created_by          text NOT NULL DEFAULT 'system',
  created_at          timestamptz DEFAULT now(),
  expires_at          timestamptz,

  -- Clinical safety layer (services/safety/*). These are separate quantities
  -- and are deliberately NOT collapsed into one number on screen:
  --   importance_score  workflow urgency   -- where it sits in the queue
  --   confidence_score  system reliability -- how much to trust the claim
  --   risk_level        clinical severity  -- how bad it is if true
  -- Rendering importance as confidence is the "decoration" failure: it looks
  -- like a trust signal while measuring queue position.
  confidence_score    float CHECK (confidence_score IS NULL
                                   OR (confidence_score >= 0.0 AND confidence_score <= 1.0)),
  confidence_band     text CHECK (confidence_band IS NULL
                                  OR confidence_band IN ('high', 'medium', 'low')),
  -- What the deterministic rules required, and what the model proposed, kept
  -- apart so a badge can always show which one set the level.
  risk_floor          text CHECK (risk_floor IS NULL
                                  OR risk_floor IN ('critical','high','medium','low','info')),
  model_risk          text CHECK (model_risk IS NULL
                                  OR model_risk IN ('critical','high','medium','low','info')),
  -- True when confidence fell below the abstention threshold. An abstained
  -- highlight is withheld from the glance view unless it is critical.
  abstained           boolean NOT NULL DEFAULT false,
  -- Triggered rules, confidence components, extraction verdict. Never note text.
  safety_metadata     jsonb DEFAULT '{}'
);

-- The glance view reads only what it will display: unabstained highlights in
-- importance order.
CREATE INDEX idx_highlights_surfaced
  ON highlights(care_note_id, abstained, importance_score DESC);

CREATE INDEX idx_highlights_care_note ON highlights(care_note_id, importance_score DESC);
CREATE INDEX idx_highlights_source    ON highlights(source_entry_id);

-- Internal clinical assessment, held apart from the patient-readable row.
--
-- care_notes.glance_cache is readable by the patient who owns it, and must be:
-- it carries their care-plan progress. But RLS is ROW-level, not column-level,
-- so anything stored in that column is readable by the patient with their own
-- JWT — a direct PostgREST call bypasses the UI entirely. Stripping the field
-- in a server component hides it from the page; it does not withhold it.
--
-- So the clinician's risk judgement ("eGFR declining 62 -> 45", CRITICAL,
-- confidence 0.92) and unresolved clinical actions live here instead, in a
-- table with NO patient policy. Postgres returns zero rows to a patient
-- whatever route they take.
CREATE TABLE care_note_assessments (
  care_note_id  uuid PRIMARY KEY REFERENCES care_notes(id) ON DELETE CASCADE,
  -- {top_items: [...], changes_since_last_visit: [...]}
  assessment    jsonb NOT NULL DEFAULT '{}',
  updated_at    timestamptz DEFAULT now()
);

-- glance_cache is patient-readable, so it must be structurally incapable of
-- holding the clinical assessment.
--
-- The assessment lives in care_note_assessments, which has no patient policy.
-- But /patients/[id] recomposes it into glance_cache in memory so downstream
-- components keep one shape, and the browser spreads that same object into its
-- care-plan writes — so a clinician ticking a checkbox once persisted the
-- assessment straight back into the column a patient reads.
--
-- Stripping it in application code has to be remembered at every write site, and
-- forgetting it is precisely what caused that. This cannot be forgotten.
CREATE OR REPLACE FUNCTION strip_internal_glance_keys()
RETURNS trigger AS $$
BEGIN
  NEW.glance_cache := COALESCE(NEW.glance_cache, '{}'::jsonb)
                      - 'top_items'
                      - 'changes_since_last_visit';
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_strip_internal_glance_keys ON care_notes;

-- BEFORE, so the row is corrected rather than the write rejected. Raising would
-- break an ordinary care-plan tick for a clinician who did nothing wrong: the
-- client is sending a superset it did not mean to send, not attacking.
CREATE TRIGGER trg_strip_internal_glance_keys
  BEFORE INSERT OR UPDATE ON care_notes
  FOR EACH ROW EXECUTE FUNCTION strip_internal_glance_keys();

CREATE TABLE interaction_log (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          uuid NOT NULL REFERENCES profiles(id),
  user_role        text NOT NULL,
  action_type      text NOT NULL CHECK (action_type IN (
                     'pin','unpin','edit','comment','accept','reject',
                     'manual_highlight','view','dismiss'
                   )),
  target_type      text NOT NULL,
  target_id        uuid NOT NULL,
  target_metadata  jsonb DEFAULT '{}',
  created_at       timestamptz DEFAULT now()
);

CREATE INDEX idx_interaction_log_user     ON interaction_log(user_id, created_at DESC);
CREATE INDEX idx_interaction_log_target   ON interaction_log(target_type, target_id);
CREATE INDEX idx_interaction_log_metadata ON interaction_log USING gin(target_metadata);

-- =============================================================================
-- 3. Helper functions  (R2: every one pins search_path)
-- =============================================================================

CREATE OR REPLACE FUNCTION get_user_clinic_id()
RETURNS uuid AS $$
  SELECT clinic_id FROM public.profiles WHERE id = auth.uid();
$$ LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public;

-- Returns text, not user_role, so every comparison site keeps working even for
-- values outside the enum (e.g. a policy testing for 'system' returns false
-- rather than raising an invalid-input error).
CREATE OR REPLACE FUNCTION get_user_role()
RETURNS text AS $$
  SELECT role::text FROM public.profiles WHERE id = auth.uid();
$$ LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public;

-- Clinic membership. SECURITY DEFINER so the care_notes/profiles reads inside
-- are NOT re-evaluated against RLS (R1 — the nested-EXISTS form raised 42501).
--
-- R3 note: this answers tenancy ONLY. It is never sufficient on its own; every
-- policy that calls it also states the roles it admits. It must never appear in
-- a patient policy — for a patient it is true for every note in the clinic,
-- which is precisely the cross-patient leak the old 007/012 policies carried.
CREATE OR REPLACE FUNCTION check_care_note_access(p_care_note_id uuid)
RETURNS boolean AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.care_notes cn
    JOIN public.profiles  p ON p.clinic_id = cn.clinic_id
    WHERE cn.id = p_care_note_id
      AND p.id  = auth.uid()
  );
$$ LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public;

-- Ownership. This is the only access helper a patient policy may use (R4).
CREATE OR REPLACE FUNCTION check_patient_owns_care_note(p_care_note_id uuid)
RETURNS boolean AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.care_notes cn
    WHERE cn.id = p_care_note_id
      AND cn.patient_id = auth.uid()
  );
$$ LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public;

-- =============================================================================
-- 4. Triggers
-- =============================================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SET search_path = public;

CREATE TRIGGER update_care_notes_updated_at
  BEFORE UPDATE ON care_notes
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_timeline_entries_updated_at
  BEFORE UPDATE ON timeline_entries
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE OR REPLACE FUNCTION update_glance_cache()
RETURNS TRIGGER AS $$
BEGIN
  UPDATE public.care_notes
  SET glance_cache_updated_at = now()
  WHERE id = COALESCE(NEW.care_note_id, OLD.care_note_id);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SET search_path = public;

CREATE TRIGGER update_glance_on_timeline
  AFTER INSERT OR UPDATE ON timeline_entries
  FOR EACH ROW EXECUTE FUNCTION update_glance_cache();

CREATE TRIGGER update_glance_on_highlight
  AFTER INSERT OR UPDATE ON highlights
  FOR EACH ROW EXECUTE FUNCTION update_glance_cache();

-- =============================================================================
-- 5. Row Level Security
-- =============================================================================

ALTER TABLE clinics          ENABLE ROW LEVEL SECURITY;
ALTER TABLE profiles         ENABLE ROW LEVEL SECURITY;
ALTER TABLE care_notes       ENABLE ROW LEVEL SECURITY;
ALTER TABLE timeline_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE note_versions    ENABLE ROW LEVEL SECURITY;
ALTER TABLE comments         ENABLE ROW LEVEL SECURITY;
ALTER TABLE highlights       ENABLE ROW LEVEL SECURITY;
ALTER TABLE interaction_log  ENABLE ROW LEVEL SECURITY;
ALTER TABLE care_note_assessments ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------- clinics ---
CREATE POLICY "Users can view their own clinic"
  ON clinics FOR SELECT
  USING (id = get_user_clinic_id());

-- --------------------------------------------------------------- profiles ---
CREATE POLICY "Users can view profiles in their clinic"
  ON profiles FOR SELECT
  USING (clinic_id = get_user_clinic_id());

CREATE POLICY "Users can update their own profile"
  ON profiles FOR UPDATE
  USING (id = auth.uid());

-- ------------------------------------------------------------- care_notes ---
CREATE POLICY "Care team can view clinic care notes"
  ON care_notes FOR SELECT
  USING (
    clinic_id = get_user_clinic_id()
    AND get_user_role() IN ('staff', 'clinician', 'admin')
  );

-- R4: ownership, not clinic.
CREATE POLICY "Patients can view only their own care note"
  ON care_notes FOR SELECT
  USING (
    get_user_role() = 'patient'
    AND patient_id = auth.uid()
  );

CREATE POLICY "Clinicians can create care notes"
  ON care_notes FOR INSERT
  WITH CHECK (
    clinic_id = get_user_clinic_id()
    AND get_user_role() IN ('clinician', 'admin')
  );

CREATE POLICY "Care team can update care notes"
  ON care_notes FOR UPDATE
  USING (
    clinic_id = get_user_clinic_id()
    AND get_user_role() IN ('clinician', 'staff', 'admin')
  );

-- ------------------------------------------------------- timeline_entries ---
CREATE POLICY "Care team can view active entries"
  ON timeline_entries FOR SELECT
  USING (
    check_care_note_access(care_note_id)
    AND get_user_role() IN ('staff', 'clinician', 'admin')
    AND is_archived = false
  );

CREATE POLICY "Clinicians and admins can view archived entries"
  ON timeline_entries FOR SELECT
  USING (
    check_care_note_access(care_note_id)
    AND get_user_role() IN ('clinician', 'admin')
    AND is_archived = true
  );

-- The patient read rule. Three constraints stack here, each load-bearing:
--   1. ownership   — check_patient_owns_care_note, never the clinic helper (R4)
--   2. visibility  — patient_visible only, and NEVER a raw AI-scribed entry.
--                    The entry_type exclusion is deliberate defence in depth:
--                    even if an AI summary were mis-marked patient_visible, it
--                    stays hidden.
--   3. readback    — carried forward from historical 012, which 014 dropped.
--                    Without it a patient's own submitted message disappears
--                    immediately after insert.
CREATE POLICY "Patients can view their own visible entries"
  ON timeline_entries FOR SELECT
  USING (
    check_patient_owns_care_note(care_note_id)
    AND get_user_role() = 'patient'
    AND is_archived = false
    AND (
      (
        visibility = 'patient_visible'
        AND entry_type NOT IN (
          'ai_doctor_consult_summary',
          'ai_nurse_consult_summary',
          'ai_patient_session_summary'
        )
      )
      OR (entry_type = 'patient_message' AND author_id = auth.uid())
    )
  );

-- `visibility = 'internal'` on both care-team INSERT policies is the enforcement
-- half of the patient-facing maker-checker gate.
--
-- Anything a patient reads has to clear grounding and prohibited-speech checks
-- first (services/safety/patient_gate.py). Those run in the AI service, which
-- writes the approved entry with the service-role key — so it bypasses RLS and
-- is unaffected by this clause. A user JWT, however, cannot create a
-- patient-visible row at all, which means the check cannot be skipped by simply
-- not calling the endpoint: a clinician's own token in curl writes nothing.
--
-- Without this the gate is advice. The browser called it, so the UI was safe,
-- but any request made outside the UI wrote straight to the patient's record.
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

CREATE POLICY "Patients can create patient messages"
  ON timeline_entries FOR INSERT
  WITH CHECK (
    check_patient_owns_care_note(care_note_id)
    AND get_user_role() = 'patient'
    AND author_role = 'patient'
    AND author_id = auth.uid()
    AND entry_type = 'patient_message'
  );

CREATE POLICY "Authors can update their own entries"
  ON timeline_entries FOR UPDATE
  USING (
    author_id = auth.uid()
    AND check_care_note_access(care_note_id)
  );

-- ---------------------------------------------------------- note_versions ---
CREATE POLICY "Care team can view versions"
  ON note_versions FOR SELECT
  USING (
    check_care_note_access(care_note_id)
    AND get_user_role() IN ('clinician', 'staff', 'admin')
  );

CREATE POLICY "Care team can create versions"
  ON note_versions FOR INSERT
  WITH CHECK (
    check_care_note_access(care_note_id)
    AND get_user_role() IN ('clinician', 'staff', 'admin')
  );

-- --------------------------------------------------------------- comments ---
-- No patient policy exists, by design. Internal discussion is invisible to
-- patients because there is no rule that could admit them.
CREATE POLICY "Care team can view comments"
  ON comments FOR SELECT
  USING (
    check_care_note_access(care_note_id)
    AND get_user_role() IN ('staff', 'clinician', 'admin')
  );

CREATE POLICY "Care team can create comments"
  ON comments FOR INSERT
  WITH CHECK (
    check_care_note_access(care_note_id)
    AND get_user_role() IN ('staff', 'clinician', 'admin')
    AND author_id = auth.uid()
  );

CREATE POLICY "Comment authors can update their comments"
  ON comments FOR UPDATE
  USING (author_id = auth.uid());

-- ------------------------------------------------------------- highlights ---
-- Also patient-invisible by omission.
CREATE POLICY "Care team can view highlights"
  ON highlights FOR SELECT
  USING (
    check_care_note_access(care_note_id)
    AND get_user_role() IN ('staff', 'clinician', 'admin')
  );

-- Tightened relative to the historical policy, which admitted any clinic
-- member including patients.
CREATE POLICY "Care team can create highlights"
  ON highlights FOR INSERT
  WITH CHECK (
    check_care_note_access(care_note_id)
    AND get_user_role() IN ('staff', 'clinician', 'admin')
  );

CREATE POLICY "Clinicians can update highlights"
  ON highlights FOR UPDATE
  USING (
    check_care_note_access(care_note_id)
    AND get_user_role() IN ('clinician', 'admin')
  );

-- ------------------------------------------------- care_note_assessments ---
-- No patient policy exists, by design. A patient reading their own care note
-- still gets nothing here, including through a direct API call, because there
-- is no rule that could admit them.
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

-- -------------------------------------------------------- interaction_log ---
CREATE POLICY "Users can view own interactions"
  ON interaction_log FOR SELECT
  USING (user_id = auth.uid());

CREATE POLICY "Admins can view clinic interactions"
  ON interaction_log FOR SELECT
  USING (
    get_user_role() = 'admin'
    AND user_id IN (
      SELECT p.id FROM public.profiles p WHERE p.clinic_id = get_user_clinic_id()
    )
  );

CREATE POLICY "Users can create own interactions"
  ON interaction_log FOR INSERT
  WITH CHECK (user_id = auth.uid());

-- =============================================================================
-- 6. Data decay / archival
-- =============================================================================
-- Historical 014 logged each run into interaction_log using an all-zero uuid.
-- That column is NOT NULL REFERENCES profiles(id) (formerly auth.users), so the
-- insert raised an FK violation and rolled the whole archival back — the policy
-- could never run (guardrails.md D1). The bogus write is removed; the function
-- reports via its return value and a NOTICE instead of fabricating a user.

CREATE OR REPLACE FUNCTION archive_old_timeline_entries()
RETURNS integer AS $$
DECLARE
  archived_count integer;
BEGIN
  UPDATE public.timeline_entries
  SET is_archived = true
  WHERE is_archived = false
    AND created_at < now() - interval '6 months'
    AND entry_type NOT IN ('instruction', 'admin')   -- keep patient-facing + admin active
    AND risk_level NOT IN ('critical', 'high');      -- keep high-risk active

  GET DIAGNOSTICS archived_count = ROW_COUNT;
  RAISE NOTICE 'archive_old_timeline_entries: archived % row(s)', archived_count;
  RETURN archived_count;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

CREATE OR REPLACE FUNCTION get_archived_entries(p_care_note_id uuid)
RETURNS SETOF public.timeline_entries AS $$
  SELECT *
  FROM public.timeline_entries
  WHERE care_note_id = p_care_note_id
    AND is_archived = true
  ORDER BY created_at DESC;
$$ LANGUAGE sql SECURITY DEFINER STABLE SET search_path = public;

COMMENT ON FUNCTION archive_old_timeline_entries() IS
'Archives timeline entries older than 6 months, except high-risk entries and
instruction/admin types. Returns the count archived. Schedule via pg_cron:
  SELECT cron.schedule(''archive-old-entries'', ''0 2 * * 0'',
                       $$SELECT archive_old_timeline_entries()$$);';

-- =============================================================================
-- 6b. Atomic version creation
-- =============================================================================
-- note_versions carries UNIQUE(care_note_id, version_number). The collab server
-- previously read MAX(version_number), added one, and inserted -- a read-then-write
-- that collides whenever two flushes land together, which under the 3s
-- collaborative debounce is the normal case rather than an edge case
-- (guardrails.md D2).
--
-- The advisory lock serialises version creation per care note, so the number is
-- allocated and consumed inside one transaction. It is taken on the care note
-- id, so unrelated notes never block each other.

CREATE OR REPLACE FUNCTION create_note_version(
  p_care_note_id     uuid,
  p_changed_by       uuid          DEFAULT NULL,
  p_content_snapshot jsonb         DEFAULT NULL,
  p_change_summary   text          DEFAULT 'Auto-saved version',
  p_yjs_snapshot     bytea         DEFAULT NULL
) RETURNS integer AS $$
DECLARE
  v_version integer;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext(p_care_note_id::text));

  SELECT COALESCE(MAX(version_number), 0) + 1
    INTO v_version
    FROM public.note_versions
   WHERE care_note_id = p_care_note_id;

  INSERT INTO public.note_versions (
    care_note_id, version_number, yjs_snapshot,
    content_snapshot, changed_by, change_summary
  ) VALUES (
    p_care_note_id, v_version, p_yjs_snapshot,
    p_content_snapshot, p_changed_by, p_change_summary
  );

  RETURN v_version;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

COMMENT ON FUNCTION create_note_version(uuid, uuid, jsonb, text, bytea) IS
'Allocates the next version_number and inserts the snapshot atomically.
Takes a per-care-note advisory lock so concurrent flushes cannot collide on
UNIQUE(care_note_id, version_number). changed_by may be NULL for system-authored
snapshots -- it is a uuid FK and must never receive a sentinel string.';

-- =============================================================================
-- 6c. Role grants
-- =============================================================================
-- RLS decides WHICH ROWS a caller sees. Grants decide whether the caller may
-- touch the table at all. Both are required, and RLS without grants fails
-- closed: PostgREST returns 42501 "permission denied for table" for every
-- request, which is what a freshly-applied schema does until this runs.
--
-- This block was missing from the first deployment of this file. The local test
-- harness grants separately, so the suites passed while the deployed database
-- was unreadable by every role. Grants belong in the migration, not in the
-- harness, precisely so the two cannot diverge again.
--
-- anon deliberately receives NOTHING beyond schema usage. Every policy in this
-- file requires auth.uid(), which is NULL for an anonymous caller, so an anon
-- grant would widen the attack surface while granting no working access.
-- Authentication happens in GoTrue, not PostgREST, so the login flow does not
-- need it either.

GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;

-- authenticated: table access gated row-by-row by the policies above.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO authenticated;

-- service_role: bypasses RLS by design; used by the collab server and the AI
-- service, both of which re-apply tenant and role checks in application code
-- (guardrails.md S3).
GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;

GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO authenticated, service_role;

-- Anything created later in this schema inherits the same posture, so a new
-- table cannot silently ship ungranted.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL ON TABLES TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT EXECUTE ON FUNCTIONS TO authenticated, service_role;

-- =============================================================================
-- 7. Clinics
-- =============================================================================

INSERT INTO clinics (id, name) VALUES
  ('c0000000-0000-0000-0000-000000000001', 'Nightingale Family Clinic'),
  ('c0000000-0000-0000-0000-000000000002', 'Sunrise Medical Center')
ON CONFLICT (id) DO NOTHING;

-- =============================================================================
-- 8. Demo seed
-- =============================================================================
-- ONE definition, ONE arity (guardrails.md M3, M5). The historical chain had a
-- 4-arg version in 013 shadowing an 8-arg version in 003; Postgres prefers the
-- exact-arity match, so the two-clinic seed became unreachable and
-- test_rbac_scope.py lost the second tenant it needs for cross-clinic denial.
--
-- All eight ids are REQUIRED. The old signature defaulted the Sunrise ids to
-- gen_random_uuid(), which could only ever fail: profiles.id references
-- auth.users, so a random uuid violates the FK. Callers must create all eight
-- auth users first — scripts/seed.sh does.
--
-- Parameters carry a p_ prefix so they cannot shadow column names inside the
-- body. Callers using PostgREST must use the p_-prefixed argument names.

CREATE OR REPLACE FUNCTION seed_demo_data(
  p_clinician_id          uuid,
  p_staff_id              uuid,
  p_patient_id            uuid,
  p_admin_id              uuid,
  p_sunrise_clinician_id  uuid,
  p_sunrise_staff_id      uuid,
  p_sunrise_patient_id    uuid,
  p_sunrise_admin_id      uuid
) RETURNS void AS $$
DECLARE
  v_clinic_1 constant uuid := 'c0000000-0000-0000-0000-000000000001';
  v_clinic_2 constant uuid := 'c0000000-0000-0000-0000-000000000002';

  v_care_note_id uuid := gen_random_uuid();
  v_entry1 uuid := gen_random_uuid();
  v_entry2 uuid := gen_random_uuid();
  v_entry3 uuid := gen_random_uuid();
  v_entry4 uuid := gen_random_uuid();
  v_entry5 uuid := gen_random_uuid();
  v_entry6 uuid := gen_random_uuid();
  v_entry7 uuid := gen_random_uuid();
  v_entry8 uuid := gen_random_uuid();

  v_sunrise_care_note_id uuid := gen_random_uuid();
  v_sunrise_entry1 uuid := gen_random_uuid();
  v_sunrise_entry2 uuid := gen_random_uuid();
  v_sunrise_entry3 uuid := gen_random_uuid();
BEGIN
  -- Idempotency guard: care_notes.patient_id is UNIQUE, so a second run would
  -- abort partway and leave the fixtures half-built.
  IF EXISTS (SELECT 1 FROM public.care_notes cn WHERE cn.patient_id = p_patient_id) THEN
    RAISE NOTICE 'seed_demo_data: already seeded, skipping';
    RETURN;
  END IF;

  -- ---------------------------------------------------------------- profiles
  INSERT INTO public.profiles (id, clinic_id, role, display_name) VALUES
    (p_clinician_id, v_clinic_1, 'clinician', 'Dr. Sarah Chen'),
    (p_staff_id,     v_clinic_1, 'staff',     'Nurse James Rivera'),
    (p_patient_id,   v_clinic_1, 'patient',   'Alice Wong'),
    (p_admin_id,     v_clinic_1, 'admin',     'Maria Santos'),
    (p_sunrise_clinician_id, v_clinic_2, 'clinician', 'Dr. James Miller'),
    (p_sunrise_staff_id,     v_clinic_2, 'staff',     'Emma Wilson'),
    (p_sunrise_patient_id,   v_clinic_2, 'patient',   'Robert Lee'),
    (p_sunrise_admin_id,     v_clinic_2, 'admin',     'Michael Brown')
  ON CONFLICT (id) DO NOTHING;

  -- =====================================================================
  -- Nightingale Family Clinic — Alice Wong
  -- =====================================================================
  -- care_plan_score is 78, on the 0-100 scale the UI renders directly.
  -- Historical 008 repaired this value; historical 013 then re-hardcoded
  -- 0.78 into the seed body, so every fresh seed reintroduced the bug and
  -- the Care Plan badge read "0.78%" in red (guardrails.md M4).
  INSERT INTO public.care_notes (id, patient_id, clinic_id, glance_cache) VALUES
    -- glance_cache holds ONLY patient-safe fields. The clinical risk assessment
    -- goes to care_note_assessments, which has no patient policy.
    (v_care_note_id, p_patient_id, v_clinic_1, '{
      "care_plan_score": 78,
      "last_visit": "2026-02-01"
    }');

  INSERT INTO public.care_note_assessments (care_note_id, assessment) VALUES
    (v_care_note_id, '{
      "top_items": [
        {"type": "action",   "text": "Cardiology referral pending since Jan 15", "risk_level": "high", "status": "unresolved"},
        {"type": "risk",     "text": "eGFR declining: 62 → 45 over 6 months", "risk_level": "critical", "confidence": 0.92},
        {"type": "positive", "text": "Blood pressure improved: 135/82 → 128/78", "risk_level": "info"}
      ]
    }');

  INSERT INTO public.timeline_entries
    (id, care_note_id, author_role, author_id, entry_type, content, content_text, risk_level, visibility, metadata, created_at) VALUES
    (v_entry1, v_care_note_id, 'clinician', p_clinician_id, 'manual_note',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Initial visit. Patient presents with hypertension (BP 145/90) and mild CKD (eGFR 62). Started on lifestyle modifications. Family history of cardiovascular disease. BMI 28.5."}]}]}',
     'Initial visit. Patient presents with hypertension (BP 145/90) and mild CKD (eGFR 62). Started on lifestyle modifications. Family history of cardiovascular disease. BMI 28.5.',
     'medium', 'internal', '{}', '2025-04-15 09:30:00+08'),

    (v_entry2, v_care_note_id, 'clinician', p_clinician_id, 'manual_note',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "3-month follow-up. BP improved to 135/82. eGFR stable at 58. Patient reports good compliance with dietary changes. Added low-dose ACE inhibitor (Lisinopril 5mg daily)."}]}]}',
     '3-month follow-up. BP improved to 135/82. eGFR stable at 58. Patient reports good compliance with dietary changes. Added low-dose ACE inhibitor (Lisinopril 5mg daily).',
     'low', 'internal', '{}', '2025-06-20 10:00:00+08'),

    (v_entry3, v_care_note_id, 'staff', p_staff_id, 'manual_note',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Vitals check: BP 130/80, HR 72, Temp 36.8°C, SpO2 98%. Patient mentioned occasional dizziness when standing up quickly. Advised to rise slowly. Noted good medication compliance — pill organizer in use."}]}]}',
     'Vitals check: BP 130/80, HR 72, Temp 36.8°C, SpO2 98%. Patient mentioned occasional dizziness when standing up quickly. Advised to rise slowly. Noted good medication compliance — pill organizer in use.',
     'low', 'internal', '{}', '2025-10-05 14:15:00+08'),

    (v_entry4, v_care_note_id, 'clinician', p_clinician_id, 'manual_note',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Lab results review: eGFR dropped to 45 (from 58 in June). Creatinine 1.4. Potassium 5.1 (borderline high). Urine albumin/creatinine ratio elevated. Increased Lisinopril to 10mg. Ordered cardiology referral for evaluation of cardiorenal syndrome. Need close monitoring of potassium."}]}]}',
     'Lab results review: eGFR dropped to 45 (from 58 in June). Creatinine 1.4. Potassium 5.1 (borderline high). Urine albumin/creatinine ratio elevated. Increased Lisinopril to 10mg. Ordered cardiology referral for evaluation of cardiorenal syndrome. Need close monitoring of potassium.',
     'critical', 'internal', '{}', '2026-01-15 11:00:00+08'),

    -- AI-scribed. author_role 'system', author_id NULL, provenance in metadata.
    -- Patients must never see this entry — enforced by entry_type exclusion in
    -- the "Patients can view their own visible entries" policy above.
    (v_entry5, v_care_note_id, 'system', NULL, 'ai_doctor_consult_summary',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "AI-Generated Consult Summary (Feb 1, 2026): Patient reports new symptom of dyspnea on exertion (climbing 1 flight of stairs). BP today 128/78 (improved). Reviewed labs — eGFR trend concerning. Cardiology referral still pending. Dr. Chen discussed potential need for nephrology consult if eGFR continues to decline. Patient education provided on fluid intake and potassium-rich foods to avoid."}]}]}',
     'AI-Generated Consult Summary (Feb 1, 2026): Patient reports new symptom of dyspnea on exertion (climbing 1 flight of stairs). BP today 128/78 (improved). Reviewed labs — eGFR trend concerning. Cardiology referral still pending. Dr. Chen discussed potential need for nephrology consult if eGFR continues to decline. Patient education provided on fluid intake and potassium-rich foods to avoid.',
     'high', 'internal',
     '{"session_id": "sess-2026-02-01-alice-chen", "ai_model": "nightingale-scribe-v1", "recording_duration_sec": 1245}',
     '2026-02-01 09:45:00+08'),

    (v_entry6, v_care_note_id, 'clinician', p_clinician_id, 'instruction',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Dear Alice, your blood pressure is looking better! Please continue taking Lisinopril 10mg every morning. Avoid foods high in potassium (bananas, oranges, potatoes) until your next blood test. Your cardiology appointment should be scheduled soon — please call us if you haven''t heard within 2 weeks. Next visit: March 2026."}]}]}',
     'Dear Alice, your blood pressure is looking better! Please continue taking Lisinopril 10mg every morning. Avoid foods high in potassium (bananas, oranges, potatoes) until your next blood test. Your cardiology appointment should be scheduled soon — please call us if you haven''t heard within 2 weeks. Next visit: March 2026.',
     'info', 'patient_visible', '{}', '2026-02-01 10:00:00+08'),

    (v_entry7, v_care_note_id, 'system', NULL, 'system_event',
     '{"test_name": "Complete Metabolic Panel", "results": [{"name": "eGFR", "value": 45, "unit": "mL/min", "reference": ">60", "abnormal": true}, {"name": "Creatinine", "value": 1.4, "unit": "mg/dL", "reference": "0.7-1.3", "abnormal": true}, {"name": "Potassium", "value": 5.1, "unit": "mEq/L", "reference": "3.5-5.0", "abnormal": true}, {"name": "Sodium", "value": 140, "unit": "mEq/L", "reference": "136-145", "abnormal": false}, {"name": "Glucose", "value": 95, "unit": "mg/dL", "reference": "70-100", "abnormal": false}]}',
     'Lab Results: Complete Metabolic Panel - eGFR 45 mL/min (low), Creatinine 1.4 mg/dL (high), Potassium 5.1 mEq/L (high), Sodium 140 mEq/L (normal), Glucose 95 mg/dL (normal)',
     'high', 'internal',
     '{"source": "lab_system", "order_id": "LAB-2026-0114-001", "lab_name": "Quest Diagnostics"}',
     '2026-01-14 08:30:00+08'),

    (v_entry8, v_care_note_id, 'system', NULL, 'system_event',
     '{"test_name": "Complete Metabolic Panel", "results": [{"name": "eGFR", "value": 58, "unit": "mL/min", "reference": ">60", "abnormal": true}, {"name": "Creatinine", "value": 1.2, "unit": "mg/dL", "reference": "0.7-1.3", "abnormal": false}, {"name": "Potassium", "value": 4.5, "unit": "mEq/L", "reference": "3.5-5.0", "abnormal": false}, {"name": "Sodium", "value": 141, "unit": "mEq/L", "reference": "136-145", "abnormal": false}]}',
     'Lab Results: Complete Metabolic Panel - eGFR 58 mL/min (low), Creatinine 1.2 mg/dL (normal), Potassium 4.5 mEq/L (normal), Sodium 141 mEq/L (normal)',
     'medium', 'internal',
     '{"source": "lab_system", "order_id": "LAB-2025-0618-001", "lab_name": "Quest Diagnostics"}',
     '2025-06-18 09:15:00+08');

  INSERT INTO public.highlights
    (care_note_id, source_entry_id, content_snippet, risk_reason, risk_level, importance_score, provenance_pointer, created_at) VALUES
    (v_care_note_id, v_entry4, 'eGFR dropped to 45 (from 58 in June)',
     'Significant decline in kidney function over 6 months suggests progressive CKD. May indicate Stage 3b transition.',
     'critical', 0.95,
     jsonb_build_object('source_type','timeline_entry','source_id',v_entry4,'span',jsonb_build_object('from',20,'to',56)),
     '2026-01-15 11:05:00+08'),
    (v_care_note_id, v_entry5, 'New symptom: dyspnea on exertion',
     'Combined with declining eGFR and hypertension history, dyspnea may indicate early cardiorenal syndrome.',
     'high', 0.88,
     jsonb_build_object('source_type','timeline_entry','source_id',v_entry5,'span',jsonb_build_object('from',65,'to',95)),
     '2026-02-01 09:50:00+08'),
    (v_care_note_id, v_entry4, 'Cardiology referral ordered',
     'Referral pending since Jan 15 — approaching 3-week mark without confirmation.',
     'high', 0.82,
     jsonb_build_object('source_type','timeline_entry','source_id',v_entry4,'span',jsonb_build_object('from',180,'to',220)),
     '2026-01-15 11:05:00+08'),
    (v_care_note_id, v_entry2, 'BP improved to 135/82',
     'Positive trend: blood pressure responding to lifestyle changes and ACE inhibitor.',
     'info', 0.45,
     jsonb_build_object('source_type','timeline_entry','source_id',v_entry2,'span',jsonb_build_object('from',20,'to',40)),
     '2025-06-20 10:05:00+08'),
    (v_care_note_id, v_entry4, 'Potassium 5.1 (borderline high)',
     'Elevated potassium with ACE inhibitor use requires monitoring — risk of hyperkalemia.',
     'medium', 0.72,
     jsonb_build_object('source_type','timeline_entry','source_id',v_entry4,'span',jsonb_build_object('from',80,'to',115)),
     '2026-01-15 11:05:00+08');

  -- content_snapshot holds the ACTUAL note text at each point in time, not a
  -- description of what changed. A snapshot containing "Added follow-up notes"
  -- cannot be reverted to -- restoring it would replace the note with that
  -- sentence. change_summary is where the description belongs.
  INSERT INTO public.note_versions
    (care_note_id, version_number, content_snapshot, changed_by, change_summary, created_at) VALUES
    (v_care_note_id, 1,
     jsonb_build_object(
       'text', 'ASSESSMENT: Hypertension (BP 145/90), mild CKD (eGFR 62). BMI 28.5. '
               'Family history of cardiovascular disease.' || chr(10) ||
               'PLAN: Lifestyle modification. Repeat bloods in 3 months.',
       'sections', jsonb_build_object(
         'assessment', 'Hypertension (BP 145/90), mild CKD (eGFR 62). BMI 28.5.',
         'plan', 'Lifestyle modification. Repeat bloods in 3 months.')),
     p_clinician_id, 'Created initial care note for Alice Wong', '2025-04-15 09:30:00+08'),

    (v_care_note_id, 2,
     jsonb_build_object(
       'text', 'ASSESSMENT: BP improved to 135/82. eGFR stable at 58. Good dietary compliance.' || chr(10) ||
               'PLAN: Started Lisinopril 5mg daily. Review in 3 months.',
       'sections', jsonb_build_object(
         'assessment', 'BP improved to 135/82. eGFR stable at 58. Good dietary compliance.',
         'plan', 'Started Lisinopril 5mg daily. Review in 3 months.')),
     p_clinician_id, 'Added 3-month follow-up, started Lisinopril', '2025-06-20 10:00:00+08'),

    (v_care_note_id, 3,
     jsonb_build_object(
       'text', 'ASSESSMENT: eGFR dropped to 45 (from 58). Creatinine 1.4. Potassium 5.1 '
               '(borderline high). Cardiorenal syndrome suspected.' || chr(10) ||
               'PLAN: Increased Lisinopril to 10mg. Cardiology referral raised. '
               'Monitor potassium closely.',
       'sections', jsonb_build_object(
         'assessment', 'eGFR dropped to 45 (from 58). Creatinine 1.4. Potassium 5.1 (borderline high). Cardiorenal syndrome suspected.',
         'plan', 'Increased Lisinopril to 10mg. Cardiology referral raised. Monitor potassium closely.')),
     p_clinician_id, 'Updated with declining eGFR results, cardiology referral', '2026-01-15 11:00:00+08'),

    (v_care_note_id, 4,
     jsonb_build_object(
       'text', 'ASSESSMENT: New dyspnea on exertion. BP 128/78 (improved). eGFR trend '
               'remains concerning. Cardiology referral still pending.' || chr(10) ||
               'PLAN: Continue Lisinopril 10mg. Nephrology consult if eGFR declines further. '
               'Patient educated on potassium-rich foods.',
       'sections', jsonb_build_object(
         'assessment', 'New dyspnea on exertion. BP 128/78 (improved). eGFR trend remains concerning. Cardiology referral still pending.',
         'plan', 'Continue Lisinopril 10mg. Nephrology consult if eGFR declines further. Patient educated on potassium-rich foods.')),
     p_clinician_id, 'Added AI-scribed summary and patient-visible instructions', '2026-02-01 10:00:00+08');

  INSERT INTO public.comments
    (care_note_id, timeline_entry_id, author_id, author_role, content, created_at) VALUES
    (v_care_note_id, v_entry4, p_staff_id, 'staff',
     'Tried calling cardiology department twice — still on waitlist. Will try again Monday.',
     '2026-01-20 15:30:00+08'),
    (v_care_note_id, v_entry4, p_clinician_id, 'clinician',
     '@Nurse James Thanks for following up. If no slot by next week, escalate to Dr. Lim directly.',
     '2026-01-20 16:00:00+08'),
    (v_care_note_id, v_entry3, p_clinician_id, 'clinician',
     'Good catch on the orthostatic dizziness. Let''s monitor — could be related to the ACE inhibitor.',
     '2025-10-05 15:00:00+08');

  INSERT INTO public.interaction_log
    (user_id, user_role, action_type, target_type, target_id, target_metadata, created_at) VALUES
    (p_clinician_id, 'clinician', 'accept', 'highlight',
     (SELECT h.id FROM public.highlights h WHERE h.care_note_id = v_care_note_id AND h.content_snippet LIKE '%eGFR dropped%' LIMIT 1),
     '{"keywords": ["eGFR", "kidney", "decline"], "topic": "renal_function"}', '2026-01-15 11:10:00+08'),
    (p_clinician_id, 'clinician', 'pin', 'highlight',
     (SELECT h.id FROM public.highlights h WHERE h.care_note_id = v_care_note_id AND h.content_snippet LIKE '%Cardiology referral%' LIMIT 1),
     '{"keywords": ["referral", "cardiology", "pending"], "topic": "referral_tracking"}', '2026-01-16 09:00:00+08'),
    (p_clinician_id, 'clinician', 'accept', 'highlight',
     (SELECT h.id FROM public.highlights h WHERE h.care_note_id = v_care_note_id AND h.content_snippet LIKE '%dyspnea%' LIMIT 1),
     '{"keywords": ["dyspnea", "exertion", "cardiac"], "topic": "symptoms"}', '2026-02-01 10:00:00+08');

  -- =====================================================================
  -- Sunrise Medical Center — Robert Lee
  -- =====================================================================
  -- The second tenant exists so cross-clinic denial is testable. Without it
  -- test_rbac_scope.py::test_cross_clinic_access_denied has nothing to assert
  -- against. care_plan_score 65, same 0-100 scale.
  INSERT INTO public.care_notes (id, patient_id, clinic_id, glance_cache) VALUES
    (v_sunrise_care_note_id, p_sunrise_patient_id, v_clinic_2, '{
      "care_plan_score": 65,
      "last_visit": "2026-01-20"
    }');

  INSERT INTO public.care_note_assessments (care_note_id, assessment) VALUES
    (v_sunrise_care_note_id, '{
      "top_items": [
        {"type": "risk",   "text": "Type 2 Diabetes - A1C trending up", "risk_level": "high", "confidence": 0.88},
        {"type": "action", "text": "Overdue for annual eye exam", "risk_level": "medium", "status": "pending"}
      ]
    }');

  INSERT INTO public.timeline_entries
    (id, care_note_id, author_role, author_id, entry_type, content, content_text, risk_level, visibility, metadata, created_at) VALUES
    (v_sunrise_entry1, v_sunrise_care_note_id, 'clinician', p_sunrise_clinician_id, 'manual_note',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "New patient transfer from out of state. Type 2 Diabetes diagnosed 3 years ago. Current medications: Metformin 1000mg BID, Lisinopril 10mg daily. A1C from previous provider: 7.8%. BMI 31.2. Patient expresses interest in weight management program."}]}]}',
     'New patient transfer from out of state. Type 2 Diabetes diagnosed 3 years ago. Current medications: Metformin 1000mg BID, Lisinopril 10mg daily. A1C from previous provider: 7.8%. BMI 31.2. Patient expresses interest in weight management program.',
     'medium', 'internal', '{}', '2025-11-10 10:00:00+08'),

    (v_sunrise_entry2, v_sunrise_care_note_id, 'system', NULL, 'system_event',
     '{"test_name": "Diabetes Panel", "results": [{"name": "A1C", "value": 8.2, "unit": "%", "reference": "<7.0", "abnormal": true}, {"name": "Fasting Glucose", "value": 156, "unit": "mg/dL", "reference": "<100", "abnormal": true}, {"name": "eGFR", "value": 72, "unit": "mL/min", "reference": ">60", "abnormal": false}]}',
     'Lab Results: Diabetes Panel - A1C 8.2% (high), Fasting Glucose 156 mg/dL (high), eGFR 72 mL/min (normal)',
     'high', 'internal',
     '{"source": "lab_system", "order_id": "LAB-2026-0120-002", "lab_name": "LabCorp"}',
     '2026-01-20 08:00:00+08'),

    (v_sunrise_entry3, v_sunrise_care_note_id, 'system', NULL, 'ai_doctor_consult_summary',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "AI-Generated Consult Summary (Jan 20, 2026): A1C increased from 7.8% to 8.2% over 2 months. Patient reports difficulty with diet adherence during holidays. Blood pressure well controlled. Dr. Miller discussed adding Ozempic for dual benefit of glycemic control and weight loss. Patient agreeable to trial. Referred to dietitian for meal planning support."}]}]}',
     'AI-Generated Consult Summary (Jan 20, 2026): A1C increased from 7.8% to 8.2% over 2 months. Patient reports difficulty with diet adherence during holidays. Blood pressure well controlled. Dr. Miller discussed adding Ozempic for dual benefit of glycemic control and weight loss. Patient agreeable to trial. Referred to dietitian for meal planning support.',
     'high', 'internal',
     '{"session_id": "sess-2026-01-20-robert-miller", "ai_model": "nightingale-scribe-v1", "recording_duration_sec": 892}',
     '2026-01-20 11:30:00+08');

  INSERT INTO public.highlights
    (care_note_id, source_entry_id, content_snippet, risk_reason, risk_level, importance_score, provenance_pointer, created_at) VALUES
    (v_sunrise_care_note_id, v_sunrise_entry2, 'A1C 8.2% (increased from 7.8%)',
     'A1C rising above target indicates worsening glycemic control. Consider treatment intensification.',
     'high', 0.90,
     jsonb_build_object('source_type','timeline_entry','source_id',v_sunrise_entry2,'span',jsonb_build_object('from',24,'to',50)),
     '2026-01-20 08:05:00+08'),
    (v_sunrise_care_note_id, v_sunrise_entry3, 'Referred to dietitian for meal planning',
     'Nutritional support is key for diabetes management. Track referral completion.',
     'medium', 0.65,
     jsonb_build_object('source_type','timeline_entry','source_id',v_sunrise_entry3,'span',jsonb_build_object('from',280,'to',320)),
     '2026-01-20 11:35:00+08');

  INSERT INTO public.note_versions
    (care_note_id, version_number, content_snapshot, changed_by, change_summary, created_at) VALUES
    (v_sunrise_care_note_id, 1,
     jsonb_build_object(
       'text', 'ASSESSMENT: Type 2 Diabetes, 3 years. A1C 7.8% from previous provider. BMI 31.2.' || chr(10) ||
               'PLAN: Continue Metformin 1000mg BID. Weight management referral.',
       'sections', jsonb_build_object(
         'assessment', 'Type 2 Diabetes, 3 years. A1C 7.8% from previous provider. BMI 31.2.',
         'plan', 'Continue Metformin 1000mg BID. Weight management referral.')),
     p_sunrise_clinician_id, 'Created care note for new patient transfer', '2025-11-10 10:00:00+08'),

    (v_sunrise_care_note_id, 2,
     jsonb_build_object(
       'text', 'ASSESSMENT: A1C risen to 8.2%. Fasting glucose 156. Diet adherence poor over holidays.' || chr(10) ||
               'PLAN: Adding Ozempic. Dietitian referral raised.',
       'sections', jsonb_build_object(
         'assessment', 'A1C risen to 8.2%. Fasting glucose 156. Diet adherence poor over holidays.',
         'plan', 'Adding Ozempic. Dietitian referral raised.')),
     p_sunrise_clinician_id, 'Added diabetes panel results, initiated Ozempic', '2026-01-20 11:30:00+08');

  INSERT INTO public.comments
    (care_note_id, timeline_entry_id, author_id, author_role, content, created_at) VALUES
    (v_sunrise_care_note_id, v_sunrise_entry3, p_sunrise_staff_id, 'staff',
     'Ozempic prior authorization submitted. Waiting 3-5 business days for approval.',
     '2026-01-21 09:00:00+08');

  RAISE NOTICE 'seed_demo_data: seeded both clinics';
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

COMMENT ON FUNCTION seed_demo_data(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid) IS
'Seeds both demo clinics. All eight auth user ids are required and must already
exist in auth.users. Idempotent: returns early if already seeded.';
