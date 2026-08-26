-- Nightingale: Shared Longitudinal Patient Note System
-- Initial Schema Migration

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- Clinics (multi-tenant scoping)
-- ============================================================
CREATE TABLE clinics (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  settings jsonb DEFAULT '{}',
  created_at timestamptz DEFAULT now()
);

-- ============================================================
-- User profiles extending Supabase auth.users
-- ============================================================
CREATE TABLE profiles (
  id uuid PRIMARY KEY REFERENCES auth.users ON DELETE CASCADE,
  clinic_id uuid NOT NULL REFERENCES clinics(id),
  role text NOT NULL CHECK (role IN ('patient', 'staff', 'clinician', 'admin')),
  display_name text NOT NULL,
  avatar_url text,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX idx_profiles_clinic ON profiles(clinic_id);
CREATE INDEX idx_profiles_role ON profiles(clinic_id, role);

-- ============================================================
-- Care Notes: One per patient (the longitudinal note)
-- ============================================================
CREATE TABLE care_notes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id uuid NOT NULL UNIQUE REFERENCES profiles(id),
  clinic_id uuid NOT NULL REFERENCES clinics(id),
  yjs_state bytea,
  glance_cache jsonb DEFAULT '{}',
  glance_cache_updated_at timestamptz DEFAULT now(),
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE INDEX idx_care_notes_patient ON care_notes(patient_id);
CREATE INDEX idx_care_notes_clinic ON care_notes(clinic_id);

-- ============================================================
-- Timeline Entries: Longitudinal entries on a care note
-- ============================================================
CREATE TABLE timeline_entries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  care_note_id uuid NOT NULL REFERENCES care_notes(id) ON DELETE CASCADE,
  author_role text NOT NULL CHECK (author_role IN ('patient', 'staff', 'clinician', 'admin', 'system')),
  author_id uuid REFERENCES auth.users,
  entry_type text NOT NULL CHECK (entry_type IN (
    'manual_note', 'ai_doctor_consult_summary', 'ai_nurse_consult_summary',
    'ai_patient_session_summary', 'instruction', 'admin', 'system_event'
  )),
  content jsonb NOT NULL DEFAULT '{}',
  content_text text,
  provenance_pointer jsonb,
  risk_level text NOT NULL DEFAULT 'info' CHECK (risk_level IN ('critical', 'high', 'medium', 'low', 'info')),
  visibility text NOT NULL DEFAULT 'internal' CHECK (visibility IN ('internal', 'patient_visible')),
  metadata jsonb DEFAULT '{}',
  is_archived boolean DEFAULT false,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE INDEX idx_timeline_entries_care_note ON timeline_entries(care_note_id, created_at DESC);
CREATE INDEX idx_timeline_entries_type ON timeline_entries(care_note_id, entry_type);
CREATE INDEX idx_timeline_entries_risk ON timeline_entries(care_note_id, risk_level);

-- ============================================================
-- Note Versions: Yjs snapshots for revision history
-- ============================================================
CREATE TABLE note_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  care_note_id uuid NOT NULL REFERENCES care_notes(id) ON DELETE CASCADE,
  version_number integer NOT NULL,
  yjs_snapshot bytea,
  content_snapshot jsonb,
  changed_by uuid REFERENCES auth.users,
  change_summary text,
  created_at timestamptz DEFAULT now(),
  UNIQUE(care_note_id, version_number)
);

CREATE INDEX idx_note_versions_care_note ON note_versions(care_note_id, version_number DESC);

-- ============================================================
-- Comments: Threaded inline comments
-- ============================================================
CREATE TABLE comments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  care_note_id uuid NOT NULL REFERENCES care_notes(id) ON DELETE CASCADE,
  timeline_entry_id uuid REFERENCES timeline_entries(id) ON DELETE CASCADE,
  parent_comment_id uuid REFERENCES comments(id) ON DELETE CASCADE,
  author_id uuid NOT NULL REFERENCES auth.users,
  author_role text NOT NULL CHECK (author_role IN ('patient', 'staff', 'clinician', 'admin')),
  content text NOT NULL,
  anchor_data jsonb,
  is_resolved boolean DEFAULT false,
  resolved_by uuid REFERENCES auth.users,
  mentions uuid[] DEFAULT '{}',
  created_at timestamptz DEFAULT now()
);

CREATE INDEX idx_comments_care_note ON comments(care_note_id, created_at DESC);
CREATE INDEX idx_comments_entry ON comments(timeline_entry_id);
CREATE INDEX idx_comments_parent ON comments(parent_comment_id);

-- ============================================================
-- Highlights: AI-generated with provenance
-- ============================================================
CREATE TABLE highlights (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  care_note_id uuid NOT NULL REFERENCES care_notes(id) ON DELETE CASCADE,
  source_entry_id uuid REFERENCES timeline_entries(id) ON DELETE SET NULL,
  content_snippet text NOT NULL,
  risk_reason text NOT NULL,
  risk_level text NOT NULL CHECK (risk_level IN ('critical', 'high', 'medium', 'low', 'info')),
  importance_score float NOT NULL DEFAULT 0.5 CHECK (importance_score >= 0.0 AND importance_score <= 1.0),
  provenance_pointer jsonb,
  is_accepted boolean,
  is_pinned boolean DEFAULT false,
  created_by text NOT NULL DEFAULT 'system',
  created_at timestamptz DEFAULT now(),
  expires_at timestamptz
);

CREATE INDEX idx_highlights_care_note ON highlights(care_note_id, importance_score DESC);
CREATE INDEX idx_highlights_source ON highlights(source_entry_id);

-- ============================================================
-- Interaction Log: Self-learning importance
-- ============================================================
CREATE TABLE interaction_log (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES auth.users,
  user_role text NOT NULL,
  action_type text NOT NULL CHECK (action_type IN (
    'pin', 'unpin', 'edit', 'comment', 'accept', 'reject',
    'manual_highlight', 'view', 'dismiss'
  )),
  target_type text NOT NULL,
  target_id uuid NOT NULL,
  target_metadata jsonb DEFAULT '{}',
  created_at timestamptz DEFAULT now()
);

CREATE INDEX idx_interaction_log_user ON interaction_log(user_id, created_at DESC);
CREATE INDEX idx_interaction_log_target ON interaction_log(target_type, target_id);
CREATE INDEX idx_interaction_log_metadata ON interaction_log USING gin(target_metadata);

-- ============================================================
-- Functions: Auto-update timestamps
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_care_notes_updated_at
  BEFORE UPDATE ON care_notes
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_timeline_entries_updated_at
  BEFORE UPDATE ON timeline_entries
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================================
-- Function: Update glance cache on timeline/highlight changes
-- ============================================================
CREATE OR REPLACE FUNCTION update_glance_cache()
RETURNS TRIGGER AS $$
BEGIN
  UPDATE care_notes
  SET glance_cache_updated_at = now()
  WHERE id = COALESCE(NEW.care_note_id, OLD.care_note_id);
  RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_glance_on_timeline
  AFTER INSERT OR UPDATE ON timeline_entries
  FOR EACH ROW EXECUTE FUNCTION update_glance_cache();

CREATE TRIGGER update_glance_on_highlight
  AFTER INSERT OR UPDATE ON highlights
  FOR EACH ROW EXECUTE FUNCTION update_glance_cache();

-- ============================================================
-- Helper function: get user's clinic_id
-- ============================================================
CREATE OR REPLACE FUNCTION get_user_clinic_id()
RETURNS uuid AS $$
  SELECT clinic_id FROM profiles WHERE id = auth.uid();
$$ LANGUAGE sql SECURITY DEFINER STABLE;

-- Helper function: get user's role
CREATE OR REPLACE FUNCTION get_user_role()
RETURNS text AS $$
  SELECT role FROM profiles WHERE id = auth.uid();
$$ LANGUAGE sql SECURITY DEFINER STABLE;
