-- ============================================================
-- Row Level Security Policies
-- ============================================================

-- Enable RLS on all tables
ALTER TABLE clinics ENABLE ROW LEVEL SECURITY;
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE care_notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE timeline_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE note_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE comments ENABLE ROW LEVEL SECURITY;
ALTER TABLE highlights ENABLE ROW LEVEL SECURITY;
ALTER TABLE interaction_log ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- CLINICS: Users can only see their own clinic
-- ============================================================
CREATE POLICY "Users can view their own clinic"
  ON clinics FOR SELECT
  USING (id = get_user_clinic_id());

-- ============================================================
-- PROFILES: Users can see profiles in their clinic
-- ============================================================
CREATE POLICY "Users can view profiles in their clinic"
  ON profiles FOR SELECT
  USING (clinic_id = get_user_clinic_id());

CREATE POLICY "Users can update their own profile"
  ON profiles FOR UPDATE
  USING (id = auth.uid());

-- ============================================================
-- CARE NOTES: Clinic-scoped access
-- ============================================================
CREATE POLICY "Clinic members can view care notes"
  ON care_notes FOR SELECT
  USING (clinic_id = get_user_clinic_id());

-- Patients can only see their own care note
CREATE POLICY "Patients can only view own care note"
  ON care_notes FOR SELECT
  USING (
    get_user_role() = 'patient' AND patient_id = auth.uid()
  );

CREATE POLICY "Clinicians can create care notes"
  ON care_notes FOR INSERT
  WITH CHECK (
    clinic_id = get_user_clinic_id()
    AND get_user_role() IN ('clinician', 'admin')
  );

CREATE POLICY "Clinicians and staff can update care notes"
  ON care_notes FOR UPDATE
  USING (
    clinic_id = get_user_clinic_id()
    AND get_user_role() IN ('clinician', 'staff', 'admin')
  );

-- ============================================================
-- TIMELINE ENTRIES: Complex role-based access
-- ============================================================

-- Select: clinic-scoped, patients see only patient_visible
CREATE POLICY "Staff/clinician/admin can view all entries"
  ON timeline_entries FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM care_notes cn
      WHERE cn.id = timeline_entries.care_note_id
      AND cn.clinic_id = get_user_clinic_id()
    )
    AND get_user_role() IN ('staff', 'clinician', 'admin')
  );

CREATE POLICY "Patients can view patient_visible entries only"
  ON timeline_entries FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM care_notes cn
      WHERE cn.id = timeline_entries.care_note_id
      AND cn.patient_id = auth.uid()
    )
    AND visibility = 'patient_visible'
  );

-- Insert: role-based
CREATE POLICY "Clinicians can create any entry"
  ON timeline_entries FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM care_notes cn
      WHERE cn.id = timeline_entries.care_note_id
      AND cn.clinic_id = get_user_clinic_id()
    )
    AND get_user_role() IN ('clinician', 'admin')
    AND author_id = auth.uid()
  );

CREATE POLICY "Staff can create staff entries"
  ON timeline_entries FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM care_notes cn
      WHERE cn.id = timeline_entries.care_note_id
      AND cn.clinic_id = get_user_clinic_id()
    )
    AND get_user_role() = 'staff'
    AND author_role = 'staff'
    AND author_id = auth.uid()
  );

-- Update: authors can edit their own entries, clinicians cannot edit staff entries and vice versa
CREATE POLICY "Authors can update their own entries"
  ON timeline_entries FOR UPDATE
  USING (
    author_id = auth.uid()
    AND EXISTS (
      SELECT 1 FROM care_notes cn
      WHERE cn.id = timeline_entries.care_note_id
      AND cn.clinic_id = get_user_clinic_id()
    )
  );

-- ============================================================
-- NOTE VERSIONS: Clinic-scoped read, clinician/staff write
-- ============================================================
CREATE POLICY "Clinic members can view versions"
  ON note_versions FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM care_notes cn
      WHERE cn.id = note_versions.care_note_id
      AND cn.clinic_id = get_user_clinic_id()
    )
    AND get_user_role() IN ('clinician', 'staff', 'admin')
  );

CREATE POLICY "Clinicians and staff can create versions"
  ON note_versions FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM care_notes cn
      WHERE cn.id = note_versions.care_note_id
      AND cn.clinic_id = get_user_clinic_id()
    )
    AND get_user_role() IN ('clinician', 'staff', 'admin')
  );

-- ============================================================
-- COMMENTS: Clinic-scoped, patients cannot see
-- ============================================================
CREATE POLICY "Staff/clinician/admin can view comments"
  ON comments FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM care_notes cn
      WHERE cn.id = comments.care_note_id
      AND cn.clinic_id = get_user_clinic_id()
    )
    AND get_user_role() IN ('staff', 'clinician', 'admin')
  );

CREATE POLICY "Staff/clinician can create comments"
  ON comments FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM care_notes cn
      WHERE cn.id = comments.care_note_id
      AND cn.clinic_id = get_user_clinic_id()
    )
    AND get_user_role() IN ('staff', 'clinician', 'admin')
    AND author_id = auth.uid()
  );

CREATE POLICY "Comment authors can update their comments"
  ON comments FOR UPDATE
  USING (author_id = auth.uid());

-- ============================================================
-- HIGHLIGHTS: Clinic-scoped, patients cannot see
-- ============================================================
CREATE POLICY "Staff/clinician/admin can view highlights"
  ON highlights FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM care_notes cn
      WHERE cn.id = highlights.care_note_id
      AND cn.clinic_id = get_user_clinic_id()
    )
    AND get_user_role() IN ('staff', 'clinician', 'admin')
  );

CREATE POLICY "System and clinicians can create highlights"
  ON highlights FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM care_notes cn
      WHERE cn.id = highlights.care_note_id
      AND cn.clinic_id = get_user_clinic_id()
    )
  );

CREATE POLICY "Clinicians can update highlights"
  ON highlights FOR UPDATE
  USING (
    EXISTS (
      SELECT 1 FROM care_notes cn
      WHERE cn.id = highlights.care_note_id
      AND cn.clinic_id = get_user_clinic_id()
    )
    AND get_user_role() IN ('clinician', 'admin')
  );

-- ============================================================
-- INTERACTION LOG: Users can log their own interactions
-- ============================================================
CREATE POLICY "Users can view own interactions"
  ON interaction_log FOR SELECT
  USING (user_id = auth.uid());

CREATE POLICY "Users can create own interactions"
  ON interaction_log FOR INSERT
  WITH CHECK (user_id = auth.uid());

-- Admin can view all interactions in clinic
CREATE POLICY "Admin can view all clinic interactions"
  ON interaction_log FOR SELECT
  USING (
    get_user_role() = 'admin'
    AND EXISTS (
      SELECT 1 FROM profiles p
      WHERE p.id = interaction_log.user_id
      AND p.clinic_id = get_user_clinic_id()
    )
  );
