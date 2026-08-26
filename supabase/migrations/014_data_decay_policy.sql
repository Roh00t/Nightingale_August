-- ============================================================
-- Data Decay / Archival Policy
-- ============================================================
-- Implements a 3-tier data storage strategy:
-- 1. Hot: Recent entries (< 6 months) - Full access, included in queries
-- 2. Warm: Archived entries (>= 6 months) - Stored in DB, excluded from default queries
-- 3. Cold: Future extension for off-DB storage (not implemented)
--
-- This migration:
-- - Creates a scheduled function to archive old entries
-- - Updates RLS policies to exclude archived entries by default
-- ============================================================

-- Function to archive timeline entries older than 6 months
CREATE OR REPLACE FUNCTION archive_old_timeline_entries()
RETURNS integer AS $$
DECLARE
  archived_count integer;
BEGIN
  UPDATE public.timeline_entries
  SET is_archived = true
  WHERE is_archived = false
    AND created_at < now() - interval '6 months'
    AND entry_type NOT IN ('instruction', 'admin')  -- Keep instructions and admin entries active
    AND risk_level NOT IN ('critical', 'high');      -- Keep high-risk entries active

  GET DIAGNOSTICS archived_count = ROW_COUNT;

  -- Log the archival action
  INSERT INTO public.interaction_log (
    user_id,
    user_role,
    action_type,
    target_type,
    target_id,
    target_metadata
  ) VALUES (
    '00000000-0000-0000-0000-000000000000'::uuid,  -- System user
    'system',
    'dismiss',
    'timeline_entry',
    gen_random_uuid(),
    jsonb_build_object(
      'action', 'bulk_archive',
      'count', archived_count,
      'archived_at', now()
    )
  );

  RETURN archived_count;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;

-- Function to retrieve archived entries (for when user explicitly requests them)
CREATE OR REPLACE FUNCTION get_archived_entries(p_care_note_id uuid)
RETURNS SETOF public.timeline_entries AS $$
  SELECT *
  FROM public.timeline_entries
  WHERE care_note_id = p_care_note_id
    AND is_archived = true
  ORDER BY created_at DESC;
$$ LANGUAGE sql SECURITY DEFINER STABLE
SET search_path = public;

-- Update the view policies to exclude archived entries by default
-- First, drop existing select policies for timeline_entries
DROP POLICY IF EXISTS "Staff/clinician/admin can view all entries" ON timeline_entries;
DROP POLICY IF EXISTS "Patients can view patient_visible entries only" ON timeline_entries;

-- Recreate with is_archived = false filter
CREATE POLICY "Staff/clinician/admin can view active entries"
  ON public.timeline_entries FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM public.care_notes cn
      WHERE cn.id = timeline_entries.care_note_id
      AND cn.clinic_id = get_user_clinic_id()
    )
    AND get_user_role() IN ('staff', 'clinician', 'admin')
    AND is_archived = false
  );

CREATE POLICY "Patients can view active patient_visible entries only"
  ON public.timeline_entries FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM public.care_notes cn
      WHERE cn.id = timeline_entries.care_note_id
      AND cn.patient_id = auth.uid()
    )
    AND visibility = 'patient_visible'
    AND is_archived = false
  );

-- Policy to allow clinicians/admins to view archived entries explicitly
CREATE POLICY "Clinicians/admins can view archived entries"
  ON public.timeline_entries FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM public.care_notes cn
      WHERE cn.id = timeline_entries.care_note_id
      AND cn.clinic_id = get_user_clinic_id()
    )
    AND get_user_role() IN ('clinician', 'admin')
    AND is_archived = true
  );

-- Add index for faster archived queries
CREATE INDEX IF NOT EXISTS idx_timeline_entries_archived
  ON public.timeline_entries(care_note_id, is_archived, created_at DESC);

-- ============================================================
-- Note: To enable automatic archival, set up a pg_cron job:
--
-- SELECT cron.schedule(
--   'archive-old-entries',
--   '0 2 * * 0',  -- Every Sunday at 2 AM
--   $$SELECT archive_old_timeline_entries()$$
-- );
--
-- Requires pg_cron extension to be enabled in Supabase Dashboard.
-- ============================================================

COMMENT ON FUNCTION archive_old_timeline_entries() IS
'Archives timeline entries older than 6 months (except high-risk and instructions).
Call manually or schedule via pg_cron. Returns count of archived entries.';

COMMENT ON FUNCTION get_archived_entries(uuid) IS
'Retrieves archived timeline entries for a specific care note.
Use when user explicitly requests historical data.';
