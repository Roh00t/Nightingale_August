-- ============================================================
-- Fix: Timeline Entries SELECT RLS Policies
-- ============================================================
-- Same nested RLS issue as the INSERT policies fixed in 006.
-- The SELECT policies also use EXISTS subqueries on care_notes
-- which are subject to care_notes RLS. Replace with the
-- check_care_note_access() SECURITY DEFINER function.
-- ============================================================

-- Drop existing SELECT policies
DROP POLICY IF EXISTS "Staff/clinician/admin can view all entries" ON timeline_entries;
DROP POLICY IF EXISTS "Patients can view patient_visible entries only" ON timeline_entries;

-- Recreate SELECT policies using SECURITY DEFINER function
CREATE POLICY "Staff/clinician/admin can view all entries"
  ON timeline_entries FOR SELECT
  USING (
    check_care_note_access(care_note_id)
    AND get_user_role() IN ('staff', 'clinician', 'admin')
  );

CREATE POLICY "Patients can view patient_visible entries only"
  ON timeline_entries FOR SELECT
  USING (
    check_care_note_access(care_note_id)
    AND get_user_role() = 'patient'
    AND visibility = 'patient_visible'
  );

-- Also fix the UPDATE policy while we're at it
DROP POLICY IF EXISTS "Authors can update their own entries" ON timeline_entries;

CREATE POLICY "Authors can update their own entries"
  ON timeline_entries FOR UPDATE
  USING (
    author_id = auth.uid()
    AND check_care_note_access(care_note_id)
  );
