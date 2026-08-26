-- Allow patients to see their own patient_message entries (for INSERT RETURNING)
DROP POLICY IF EXISTS "Patients can view patient_visible entries only" ON timeline_entries;

CREATE POLICY "Patients can view patient_visible entries only"
  ON timeline_entries FOR SELECT
  USING (
    check_care_note_access(care_note_id)
    AND get_user_role() = 'patient'
    AND (
      visibility = 'patient_visible'
      OR (entry_type = 'patient_message' AND author_id = auth.uid())
    )
  );
