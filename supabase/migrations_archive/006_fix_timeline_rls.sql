-- ============================================================
-- Fix: Timeline Entries INSERT RLS Policy
-- ============================================================
-- Problem: The INSERT policies on timeline_entries use an EXISTS
-- subquery on care_notes, which is itself subject to care_notes
-- RLS. This nested RLS evaluation fails with error 42501.
--
-- Solution: Create a SECURITY DEFINER function that checks
-- whether the current user has access to a given care_note_id
-- (same clinic membership). SECURITY DEFINER bypasses RLS on
-- the tables it queries, eliminating the nested RLS issue.
-- ============================================================

-- 1. Create the SECURITY DEFINER access-check function
CREATE OR REPLACE FUNCTION check_care_note_access(p_care_note_id uuid)
RETURNS boolean AS $$
  SELECT EXISTS (
    SELECT 1
    FROM care_notes cn
    JOIN profiles p ON p.clinic_id = cn.clinic_id
    WHERE cn.id = p_care_note_id
      AND p.id = auth.uid()
  );
$$ LANGUAGE sql SECURITY DEFINER STABLE;

-- 2. Drop the existing INSERT policies that use nested EXISTS
DROP POLICY IF EXISTS "Clinicians can create any entry" ON timeline_entries;
DROP POLICY IF EXISTS "Staff can create staff entries" ON timeline_entries;

-- 3. Recreate INSERT policies using the SECURITY DEFINER function
CREATE POLICY "Clinicians can create any entry"
  ON timeline_entries FOR INSERT
  WITH CHECK (
    check_care_note_access(care_note_id)
    AND get_user_role() IN ('clinician', 'admin')
    AND author_id = auth.uid()
  );

CREATE POLICY "Staff can create staff entries"
  ON timeline_entries FOR INSERT
  WITH CHECK (
    check_care_note_access(care_note_id)
    AND get_user_role() = 'staff'
    AND author_role = 'staff'
    AND author_id = auth.uid()
  );
