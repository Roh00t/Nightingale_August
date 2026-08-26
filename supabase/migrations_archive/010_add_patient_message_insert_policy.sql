-- Allow patients to submit patient_message entries for their own care note
CREATE OR REPLACE FUNCTION check_patient_owns_care_note(p_care_note_id uuid)
RETURNS boolean AS $$
  SELECT EXISTS (
    SELECT 1
    FROM care_notes cn
    WHERE cn.id = p_care_note_id
      AND cn.patient_id = auth.uid()
  );
$$ LANGUAGE sql SECURITY DEFINER STABLE;

DROP POLICY IF EXISTS "Patients can create patient messages" ON timeline_entries;

CREATE POLICY "Patients can create patient messages"
  ON timeline_entries FOR INSERT
  WITH CHECK (
    check_patient_owns_care_note(care_note_id)
    AND get_user_role() = 'patient'
    AND author_role = 'patient'
    AND author_id = auth.uid()
    AND entry_type = 'patient_message'
  );
