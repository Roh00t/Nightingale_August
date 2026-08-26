-- Restrict care note select to staff/clinician/admin only
DROP POLICY IF EXISTS "Clinic members can view care notes" ON care_notes;

CREATE POLICY "Clinic members can view care notes"
  ON care_notes FOR SELECT
  USING (
    clinic_id = get_user_clinic_id()
    AND get_user_role() IN ('staff', 'clinician', 'admin')
  );
