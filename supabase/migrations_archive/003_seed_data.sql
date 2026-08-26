-- ============================================================
-- Seed Data for Development/Demo
-- ============================================================
-- NOTE: This seed file uses raw UUIDs and assumes Supabase auth users
-- are created via the auth API first. In development, use the
-- Supabase dashboard or API to create these auth users, then run this.

-- We use fixed UUIDs for reproducibility in tests/demos:
--
-- Nightingale Family Clinic (c0000000-0000-0000-0000-000000000001):
--   Dr. Sarah Chen (clinician): u0000000-0000-0000-0000-000000000001
--   Nurse James (staff):        u0000000-0000-0000-0000-000000000002
--   Alice Wong (patient):       u0000000-0000-0000-0000-000000000003
--   Admin Maria (admin):        u0000000-0000-0000-0000-000000000004
--
-- Sunrise Medical Center (c0000000-0000-0000-0000-000000000002):
--   Dr. James Miller (clinician): u0000000-0000-0000-0000-000000000005
--   Emma Wilson (staff):          u0000000-0000-0000-0000-000000000006
--   Robert Lee (patient):         u0000000-0000-0000-0000-000000000007
--   Michael Brown (admin):        u0000000-0000-0000-0000-000000000008

-- ============================================================
-- Clinics
-- ============================================================
INSERT INTO clinics (id, name) VALUES
  ('c0000000-0000-0000-0000-000000000001', 'Nightingale Family Clinic'),
  ('c0000000-0000-0000-0000-000000000002', 'Sunrise Medical Center');

-- ============================================================
-- Profiles (assumes auth.users already exist)
-- ============================================================
-- These will be created by the seed script that also creates auth users
-- For now, we prepare the data as a reference

-- ============================================================
-- Care Note for Alice Wong
-- ============================================================
-- Will be inserted after profiles are created

-- ============================================================
-- Function to create seed data after auth users exist
-- ============================================================
CREATE OR REPLACE FUNCTION seed_demo_data(
  clinician_id uuid,
  staff_id uuid,
  patient_id uuid,
  admin_id uuid,
  -- Sunrise Medical Center users (optional - defaults to generated UUIDs)
  sunrise_clinician_id uuid DEFAULT gen_random_uuid(),
  sunrise_staff_id uuid DEFAULT gen_random_uuid(),
  sunrise_patient_id uuid DEFAULT gen_random_uuid(),
  sunrise_admin_id uuid DEFAULT gen_random_uuid()
) RETURNS void AS $$
DECLARE
  care_note_id uuid := gen_random_uuid();
  entry1_id uuid := gen_random_uuid();
  entry2_id uuid := gen_random_uuid();
  entry3_id uuid := gen_random_uuid();
  entry4_id uuid := gen_random_uuid();
  entry5_id uuid := gen_random_uuid();
  entry6_id uuid := gen_random_uuid();
  entry7_id uuid := gen_random_uuid();
  entry8_id uuid := gen_random_uuid();
  -- Sunrise Medical Center data
  sunrise_care_note_id uuid := gen_random_uuid();
  sunrise_entry1_id uuid := gen_random_uuid();
  sunrise_entry2_id uuid := gen_random_uuid();
  sunrise_entry3_id uuid := gen_random_uuid();
BEGIN
  -- Profiles for Nightingale Family Clinic
  INSERT INTO profiles (id, clinic_id, role, display_name) VALUES
    (clinician_id, 'c0000000-0000-0000-0000-000000000001', 'clinician', 'Dr. Sarah Chen'),
    (staff_id, 'c0000000-0000-0000-0000-000000000001', 'staff', 'Nurse James Rivera'),
    (patient_id, 'c0000000-0000-0000-0000-000000000001', 'patient', 'Alice Wong'),
    (admin_id, 'c0000000-0000-0000-0000-000000000001', 'admin', 'Maria Santos')
  ON CONFLICT (id) DO NOTHING;

  -- Profiles for Sunrise Medical Center
  INSERT INTO profiles (id, clinic_id, role, display_name) VALUES
    (sunrise_clinician_id, 'c0000000-0000-0000-0000-000000000002', 'clinician', 'Dr. James Miller'),
    (sunrise_staff_id, 'c0000000-0000-0000-0000-000000000002', 'staff', 'Emma Wilson'),
    (sunrise_patient_id, 'c0000000-0000-0000-0000-000000000002', 'patient', 'Robert Lee'),
    (sunrise_admin_id, 'c0000000-0000-0000-0000-000000000002', 'admin', 'Michael Brown')
  ON CONFLICT (id) DO NOTHING;

  -- Care Note
  INSERT INTO care_notes (id, patient_id, clinic_id, glance_cache) VALUES
    (care_note_id, patient_id, 'c0000000-0000-0000-0000-000000000001', '{
      "top_items": [
        {"type": "action", "text": "Cardiology referral pending since Jan 15", "risk_level": "high", "status": "unresolved"},
        {"type": "risk", "text": "eGFR declining: 62 → 45 over 6 months", "risk_level": "critical", "confidence": 0.92},
        {"type": "positive", "text": "Blood pressure improved: 135/82 → 128/78", "risk_level": "info"}
      ],
      "care_plan_score": 0.78,
      "last_visit": "2026-02-01"
    }');

  -- Timeline Entries spanning multiple dates
  INSERT INTO timeline_entries (id, care_note_id, author_role, author_id, entry_type, content, content_text, risk_level, visibility, metadata, created_at) VALUES
    -- April 2025: Initial visit
    (entry1_id, care_note_id, 'clinician', clinician_id, 'manual_note',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Initial visit. Patient presents with hypertension (BP 145/90) and mild CKD (eGFR 62). Started on lifestyle modifications. Family history of cardiovascular disease. BMI 28.5."}]}]}',
     'Initial visit. Patient presents with hypertension (BP 145/90) and mild CKD (eGFR 62). Started on lifestyle modifications. Family history of cardiovascular disease. BMI 28.5.',
     'medium', 'internal', '{}', '2025-04-15 09:30:00+08'),

    -- June 2025: Follow-up
    (entry2_id, care_note_id, 'clinician', clinician_id, 'manual_note',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "3-month follow-up. BP improved to 135/82. eGFR stable at 58. Patient reports good compliance with dietary changes. Added low-dose ACE inhibitor (Lisinopril 5mg daily)."}]}]}',
     '3-month follow-up. BP improved to 135/82. eGFR stable at 58. Patient reports good compliance with dietary changes. Added low-dose ACE inhibitor (Lisinopril 5mg daily).',
     'low', 'internal', '{}', '2025-06-20 10:00:00+08'),

    -- October 2025: Staff observation
    (entry3_id, care_note_id, 'staff', staff_id, 'manual_note',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Vitals check: BP 130/80, HR 72, Temp 36.8°C, SpO2 98%. Patient mentioned occasional dizziness when standing up quickly. Advised to rise slowly. Noted good medication compliance — pill organizer in use."}]}]}',
     'Vitals check: BP 130/80, HR 72, Temp 36.8°C, SpO2 98%. Patient mentioned occasional dizziness when standing up quickly. Advised to rise slowly. Noted good medication compliance — pill organizer in use.',
     'low', 'internal', '{}', '2025-10-05 14:15:00+08'),

    -- January 2026: Concerning labs
    (entry4_id, care_note_id, 'clinician', clinician_id, 'manual_note',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Lab results review: eGFR dropped to 45 (from 58 in June). Creatinine 1.4. Potassium 5.1 (borderline high). Urine albumin/creatinine ratio elevated. Increased Lisinopril to 10mg. Ordered cardiology referral for evaluation of cardiorenal syndrome. Need close monitoring of potassium."}]}]}',
     'Lab results review: eGFR dropped to 45 (from 58 in June). Creatinine 1.4. Potassium 5.1 (borderline high). Urine albumin/creatinine ratio elevated. Increased Lisinopril to 10mg. Ordered cardiology referral for evaluation of cardiorenal syndrome. Need close monitoring of potassium.',
     'critical', 'internal', '{}', '2026-01-15 11:00:00+08'),

    -- February 2026: AI-scribed consult summary (with session_id for provenance)
    (entry5_id, care_note_id, 'system', NULL, 'ai_doctor_consult_summary',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "AI-Generated Consult Summary (Feb 1, 2026): Patient reports new symptom of dyspnea on exertion (climbing 1 flight of stairs). BP today 128/78 (improved). Reviewed labs — eGFR trend concerning. Cardiology referral still pending. Dr. Chen discussed potential need for nephrology consult if eGFR continues to decline. Patient education provided on fluid intake and potassium-rich foods to avoid."}]}]}',
     'AI-Generated Consult Summary (Feb 1, 2026): Patient reports new symptom of dyspnea on exertion (climbing 1 flight of stairs). BP today 128/78 (improved). Reviewed labs — eGFR trend concerning. Cardiology referral still pending. Dr. Chen discussed potential need for nephrology consult if eGFR continues to decline. Patient education provided on fluid intake and potassium-rich foods to avoid.',
     'high', 'internal', '{"session_id": "sess-2026-02-01-alice-chen", "ai_model": "nightingale-scribe-v1", "recording_duration_sec": 1245}', '2026-02-01 09:45:00+08'),

    -- Patient-visible instruction
    (entry6_id, care_note_id, 'clinician', clinician_id, 'instruction',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Dear Alice, your blood pressure is looking better! Please continue taking Lisinopril 10mg every morning. Avoid foods high in potassium (bananas, oranges, potatoes) until your next blood test. Your cardiology appointment should be scheduled soon — please call us if you haven''t heard within 2 weeks. Next visit: March 2026."}]}]}',
     'Dear Alice, your blood pressure is looking better! Please continue taking Lisinopril 10mg every morning. Avoid foods high in potassium (bananas, oranges, potatoes) until your next blood test. Your cardiology appointment should be scheduled soon — please call us if you haven''t heard within 2 weeks. Next visit: March 2026.',
     'info', 'patient_visible', '{}', '2026-02-01 10:00:00+08'),

    -- Lab results as system events (January 2026)
    (entry7_id, care_note_id, 'system', NULL, 'system_event',
     '{"test_name": "Complete Metabolic Panel", "results": [{"name": "eGFR", "value": 45, "unit": "mL/min", "reference": ">60", "abnormal": true}, {"name": "Creatinine", "value": 1.4, "unit": "mg/dL", "reference": "0.7-1.3", "abnormal": true}, {"name": "Potassium", "value": 5.1, "unit": "mEq/L", "reference": "3.5-5.0", "abnormal": true}, {"name": "Sodium", "value": 140, "unit": "mEq/L", "reference": "136-145", "abnormal": false}, {"name": "Glucose", "value": 95, "unit": "mg/dL", "reference": "70-100", "abnormal": false}]}',
     'Lab Results: Complete Metabolic Panel - eGFR 45 mL/min (low), Creatinine 1.4 mg/dL (high), Potassium 5.1 mEq/L (high), Sodium 140 mEq/L (normal), Glucose 95 mg/dL (normal)',
     'high', 'internal', '{"source": "lab_system", "order_id": "LAB-2026-0114-001", "lab_name": "Quest Diagnostics"}', '2026-01-14 08:30:00+08'),

    -- Earlier lab results (June 2025) - for comparison
    (entry8_id, care_note_id, 'system', NULL, 'system_event',
     '{"test_name": "Complete Metabolic Panel", "results": [{"name": "eGFR", "value": 58, "unit": "mL/min", "reference": ">60", "abnormal": true}, {"name": "Creatinine", "value": 1.2, "unit": "mg/dL", "reference": "0.7-1.3", "abnormal": false}, {"name": "Potassium", "value": 4.5, "unit": "mEq/L", "reference": "3.5-5.0", "abnormal": false}, {"name": "Sodium", "value": 141, "unit": "mEq/L", "reference": "136-145", "abnormal": false}]}',
     'Lab Results: Complete Metabolic Panel - eGFR 58 mL/min (low), Creatinine 1.2 mg/dL (normal), Potassium 4.5 mEq/L (normal), Sodium 141 mEq/L (normal)',
     'medium', 'internal', '{"source": "lab_system", "order_id": "LAB-2025-0618-001", "lab_name": "Quest Diagnostics"}', '2025-06-18 09:15:00+08');

  -- Highlights
  INSERT INTO highlights (care_note_id, source_entry_id, content_snippet, risk_reason, risk_level, importance_score, provenance_pointer, created_at) VALUES
    (care_note_id, entry4_id,
     'eGFR dropped to 45 (from 58 in June)',
     'Significant decline in kidney function over 6 months suggests progressive CKD. May indicate Stage 3b transition.',
     'critical', 0.95,
     jsonb_build_object('source_type', 'timeline_entry', 'source_id', entry4_id, 'span', jsonb_build_object('from', 20, 'to', 56)),
     '2026-01-15 11:05:00+08'),

    (care_note_id, entry5_id,
     'New symptom: dyspnea on exertion',
     'Combined with declining eGFR and hypertension history, dyspnea may indicate early cardiorenal syndrome.',
     'high', 0.88,
     jsonb_build_object('source_type', 'timeline_entry', 'source_id', entry5_id, 'span', jsonb_build_object('from', 65, 'to', 95)),
     '2026-02-01 09:50:00+08'),

    (care_note_id, entry4_id,
     'Cardiology referral ordered',
     'Referral pending since Jan 15 — approaching 3-week mark without confirmation.',
     'high', 0.82,
     jsonb_build_object('source_type', 'timeline_entry', 'source_id', entry4_id, 'span', jsonb_build_object('from', 180, 'to', 220)),
     '2026-01-15 11:05:00+08'),

    (care_note_id, entry2_id,
     'BP improved to 135/82',
     'Positive trend: blood pressure responding to lifestyle changes and ACE inhibitor.',
     'info', 0.45,
     jsonb_build_object('source_type', 'timeline_entry', 'source_id', entry2_id, 'span', jsonb_build_object('from', 20, 'to', 40)),
     '2025-06-20 10:05:00+08'),

    (care_note_id, entry4_id,
     'Potassium 5.1 (borderline high)',
     'Elevated potassium with ACE inhibitor use requires monitoring — risk of hyperkalemia.',
     'medium', 0.72,
     jsonb_build_object('source_type', 'timeline_entry', 'source_id', entry4_id, 'span', jsonb_build_object('from', 80, 'to', 115)),
     '2026-01-15 11:05:00+08');

  -- Version history
  INSERT INTO note_versions (care_note_id, version_number, content_snapshot, changed_by, change_summary, created_at) VALUES
    (care_note_id, 1, '{"summary": "Initial patient record created"}', clinician_id, 'Created initial care note for Alice Wong', '2025-04-15 09:30:00+08'),
    (care_note_id, 2, '{"summary": "Added follow-up notes and medication"}', clinician_id, 'Added 3-month follow-up, started Lisinopril', '2025-06-20 10:00:00+08'),
    (care_note_id, 3, '{"summary": "Added concerning lab results"}', clinician_id, 'Updated with declining eGFR results, cardiology referral', '2026-01-15 11:00:00+08'),
    (care_note_id, 4, '{"summary": "AI consult summary and patient instructions"}', clinician_id, 'Added AI-scribed summary and patient-visible instructions', '2026-02-01 10:00:00+08');

  -- Comments
  INSERT INTO comments (care_note_id, timeline_entry_id, author_id, author_role, content, created_at) VALUES
    (care_note_id, entry4_id, staff_id, 'staff',
     'Tried calling cardiology department twice — still on waitlist. Will try again Monday.',
     '2026-01-20 15:30:00+08'),
    (care_note_id, entry4_id, clinician_id, 'clinician',
     '@Nurse James Thanks for following up. If no slot by next week, escalate to Dr. Lim directly.',
     '2026-01-20 16:00:00+08'),
    (care_note_id, entry3_id, clinician_id, 'clinician',
     'Good catch on the orthostatic dizziness. Let''s monitor — could be related to the ACE inhibitor.',
     '2025-10-05 15:00:00+08');

  -- Interaction log entries (for self-learning demo)
  INSERT INTO interaction_log (user_id, user_role, action_type, target_type, target_id, target_metadata, created_at) VALUES
    (clinician_id, 'clinician', 'accept', 'highlight', (SELECT id FROM highlights WHERE content_snippet LIKE '%eGFR dropped%' LIMIT 1),
     '{"keywords": ["eGFR", "kidney", "decline"], "topic": "renal_function"}', '2026-01-15 11:10:00+08'),
    (clinician_id, 'clinician', 'pin', 'highlight', (SELECT id FROM highlights WHERE content_snippet LIKE '%Cardiology referral%' LIMIT 1),
     '{"keywords": ["referral", "cardiology", "pending"], "topic": "referral_tracking"}', '2026-01-16 09:00:00+08'),
    (clinician_id, 'clinician', 'accept', 'highlight', (SELECT id FROM highlights WHERE content_snippet LIKE '%dyspnea%' LIMIT 1),
     '{"keywords": ["dyspnea", "exertion", "cardiac"], "topic": "symptoms"}', '2026-02-01 10:00:00+08');

  -- ============================================================
  -- SUNRISE MEDICAL CENTER DATA
  -- Demonstrates multi-clinic isolation (RLS policies)
  -- ============================================================

  -- Care Note for Robert Lee (Sunrise patient)
  INSERT INTO care_notes (id, patient_id, clinic_id, glance_cache) VALUES
    (sunrise_care_note_id, sunrise_patient_id, 'c0000000-0000-0000-0000-000000000002', '{
      "top_items": [
        {"type": "risk", "text": "Type 2 Diabetes - A1C trending up", "risk_level": "high", "confidence": 0.88},
        {"type": "action", "text": "Overdue for annual eye exam", "risk_level": "medium", "status": "pending"}
      ],
      "care_plan_score": 0.65,
      "last_visit": "2026-01-20"
    }');

  -- Timeline Entries for Robert Lee
  INSERT INTO timeline_entries (id, care_note_id, author_role, author_id, entry_type, content, content_text, risk_level, visibility, metadata, created_at) VALUES
    -- Initial diabetes management visit
    (sunrise_entry1_id, sunrise_care_note_id, 'clinician', sunrise_clinician_id, 'manual_note',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "New patient transfer from out of state. Type 2 Diabetes diagnosed 3 years ago. Current medications: Metformin 1000mg BID, Lisinopril 10mg daily. A1C from previous provider: 7.8%. BMI 31.2. Patient expresses interest in weight management program."}]}]}',
     'New patient transfer from out of state. Type 2 Diabetes diagnosed 3 years ago. Current medications: Metformin 1000mg BID, Lisinopril 10mg daily. A1C from previous provider: 7.8%. BMI 31.2. Patient expresses interest in weight management program.',
     'medium', 'internal', '{}', '2025-11-10 10:00:00+08'),

    -- Lab results
    (sunrise_entry2_id, sunrise_care_note_id, 'system', NULL, 'system_event',
     '{"test_name": "Diabetes Panel", "results": [{"name": "A1C", "value": 8.2, "unit": "%", "reference": "<7.0", "abnormal": true}, {"name": "Fasting Glucose", "value": 156, "unit": "mg/dL", "reference": "<100", "abnormal": true}, {"name": "eGFR", "value": 72, "unit": "mL/min", "reference": ">60", "abnormal": false}]}',
     'Lab Results: Diabetes Panel - A1C 8.2% (high), Fasting Glucose 156 mg/dL (high), eGFR 72 mL/min (normal)',
     'high', 'internal', '{"source": "lab_system", "order_id": "LAB-2026-0120-002", "lab_name": "LabCorp"}', '2026-01-20 08:00:00+08'),

    -- Follow-up with AI summary
    (sunrise_entry3_id, sunrise_care_note_id, 'system', NULL, 'ai_doctor_consult_summary',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "AI-Generated Consult Summary (Jan 20, 2026): A1C increased from 7.8% to 8.2% over 2 months. Patient reports difficulty with diet adherence during holidays. Blood pressure well controlled. Dr. Miller discussed adding Ozempic for dual benefit of glycemic control and weight loss. Patient agreeable to trial. Referred to dietitian for meal planning support."}]}]}',
     'AI-Generated Consult Summary (Jan 20, 2026): A1C increased from 7.8% to 8.2% over 2 months. Patient reports difficulty with diet adherence during holidays. Blood pressure well controlled. Dr. Miller discussed adding Ozempic for dual benefit of glycemic control and weight loss. Patient agreeable to trial. Referred to dietitian for meal planning support.',
     'high', 'internal', '{"session_id": "sess-2026-01-20-robert-miller", "ai_model": "nightingale-scribe-v1", "recording_duration_sec": 892}', '2026-01-20 11:30:00+08');

  -- Highlights for Robert Lee
  INSERT INTO highlights (care_note_id, source_entry_id, content_snippet, risk_reason, risk_level, importance_score, provenance_pointer, created_at) VALUES
    (sunrise_care_note_id, sunrise_entry2_id,
     'A1C 8.2% (increased from 7.8%)',
     'A1C rising above target indicates worsening glycemic control. Consider treatment intensification.',
     'high', 0.90,
     jsonb_build_object('source_type', 'timeline_entry', 'source_id', sunrise_entry2_id, 'span', jsonb_build_object('from', 24, 'to', 50)),
     '2026-01-20 08:05:00+08'),

    (sunrise_care_note_id, sunrise_entry3_id,
     'Referred to dietitian for meal planning',
     'Nutritional support is key for diabetes management. Track referral completion.',
     'medium', 0.65,
     jsonb_build_object('source_type', 'timeline_entry', 'source_id', sunrise_entry3_id, 'span', jsonb_build_object('from', 280, 'to', 320)),
     '2026-01-20 11:35:00+08');

  -- Version history for Sunrise
  INSERT INTO note_versions (care_note_id, version_number, content_snapshot, changed_by, change_summary, created_at) VALUES
    (sunrise_care_note_id, 1, '{"summary": "Initial patient record for Robert Lee"}', sunrise_clinician_id, 'Created care note for new patient transfer', '2025-11-10 10:00:00+08'),
    (sunrise_care_note_id, 2, '{"summary": "Updated with lab results and treatment plan"}', sunrise_clinician_id, 'Added diabetes panel results, initiated Ozempic', '2026-01-20 11:30:00+08');

  -- Comments for Sunrise
  INSERT INTO comments (care_note_id, timeline_entry_id, author_id, author_role, content, created_at) VALUES
    (sunrise_care_note_id, sunrise_entry3_id, sunrise_staff_id, 'staff',
     'Ozempic prior authorization submitted. Waiting 3-5 business days for approval.',
     '2026-01-21 09:00:00+08');

END;
$$ LANGUAGE plpgsql;
