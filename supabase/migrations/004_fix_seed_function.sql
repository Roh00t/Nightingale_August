-- Fix seed_demo_data function: use jsonb_build_object instead of text concatenation
DROP FUNCTION IF EXISTS seed_demo_data(uuid, uuid, uuid, uuid);

CREATE OR REPLACE FUNCTION seed_demo_data(
  clinician_id uuid,
  staff_id uuid,
  patient_id uuid,
  admin_id uuid
) RETURNS void AS $$
DECLARE
  care_note_id uuid := gen_random_uuid();
  entry1_id uuid := gen_random_uuid();
  entry2_id uuid := gen_random_uuid();
  entry3_id uuid := gen_random_uuid();
  entry4_id uuid := gen_random_uuid();
  entry5_id uuid := gen_random_uuid();
  entry6_id uuid := gen_random_uuid();
BEGIN
  -- Profiles
  INSERT INTO profiles (id, clinic_id, role, display_name) VALUES
    (clinician_id, 'c0000000-0000-0000-0000-000000000001', 'clinician', 'Dr. Sarah Chen'),
    (staff_id, 'c0000000-0000-0000-0000-000000000001', 'staff', 'Nurse James Rivera'),
    (patient_id, 'c0000000-0000-0000-0000-000000000001', 'patient', 'Alice Wong'),
    (admin_id, 'c0000000-0000-0000-0000-000000000001', 'admin', 'Maria Santos')
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

  -- Timeline Entries
  INSERT INTO timeline_entries (id, care_note_id, author_role, author_id, entry_type, content, content_text, risk_level, visibility, created_at) VALUES
    (entry1_id, care_note_id, 'clinician', clinician_id, 'manual_note',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Initial visit. Patient presents with hypertension (BP 145/90) and mild CKD (eGFR 62). Started on lifestyle modifications. Family history of cardiovascular disease. BMI 28.5."}]}]}',
     'Initial visit. Patient presents with hypertension (BP 145/90) and mild CKD (eGFR 62). Started on lifestyle modifications. Family history of cardiovascular disease. BMI 28.5.',
     'medium', 'internal', '2025-04-15 09:30:00+08'),

    (entry2_id, care_note_id, 'clinician', clinician_id, 'manual_note',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "3-month follow-up. BP improved to 135/82. eGFR stable at 58. Patient reports good compliance with dietary changes. Added low-dose ACE inhibitor (Lisinopril 5mg daily)."}]}]}',
     '3-month follow-up. BP improved to 135/82. eGFR stable at 58. Patient reports good compliance with dietary changes. Added low-dose ACE inhibitor (Lisinopril 5mg daily).',
     'low', 'internal', '2025-06-20 10:00:00+08'),

    (entry3_id, care_note_id, 'staff', staff_id, 'manual_note',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Vitals check: BP 130/80, HR 72, Temp 36.8°C, SpO2 98%. Patient mentioned occasional dizziness when standing up quickly. Advised to rise slowly. Noted good medication compliance — pill organizer in use."}]}]}',
     'Vitals check: BP 130/80, HR 72, Temp 36.8°C, SpO2 98%. Patient mentioned occasional dizziness when standing up quickly. Advised to rise slowly. Noted good medication compliance — pill organizer in use.',
     'low', 'internal', '2025-10-05 14:15:00+08'),

    (entry4_id, care_note_id, 'clinician', clinician_id, 'manual_note',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Lab results review: eGFR dropped to 45 (from 58 in June). Creatinine 1.4. Potassium 5.1 (borderline high). Urine albumin/creatinine ratio elevated. Increased Lisinopril to 10mg. Ordered cardiology referral for evaluation of cardiorenal syndrome. Need close monitoring of potassium."}]}]}',
     'Lab results review: eGFR dropped to 45 (from 58 in June). Creatinine 1.4. Potassium 5.1 (borderline high). Urine albumin/creatinine ratio elevated. Increased Lisinopril to 10mg. Ordered cardiology referral for evaluation of cardiorenal syndrome. Need close monitoring of potassium.',
     'critical', 'internal', '2026-01-15 11:00:00+08'),

    (entry5_id, care_note_id, 'system', NULL, 'ai_doctor_consult_summary',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "AI-Generated Consult Summary (Feb 1, 2026): Patient reports new symptom of dyspnea on exertion (climbing 1 flight of stairs). BP today 128/78 (improved). Reviewed labs — eGFR trend concerning. Cardiology referral still pending. Dr. Chen discussed potential need for nephrology consult if eGFR continues to decline. Patient education provided on fluid intake and potassium-rich foods to avoid."}]}]}',
     'AI-Generated Consult Summary (Feb 1, 2026): Patient reports new symptom of dyspnea on exertion (climbing 1 flight of stairs). BP today 128/78 (improved). Reviewed labs — eGFR trend concerning. Cardiology referral still pending. Dr. Chen discussed potential need for nephrology consult if eGFR continues to decline. Patient education provided on fluid intake and potassium-rich foods to avoid.',
     'high', 'internal', '2026-02-01 09:45:00+08'),

    (entry6_id, care_note_id, 'clinician', clinician_id, 'instruction',
     '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Dear Alice, your blood pressure is looking better! Please continue taking Lisinopril 10mg every morning. Avoid foods high in potassium (bananas, oranges, potatoes) until your next blood test. Your cardiology appointment should be scheduled soon — please call us if you haven''t heard within 2 weeks. Next visit: March 2026."}]}]}',
     'Dear Alice, your blood pressure is looking better! Please continue taking Lisinopril 10mg every morning. Avoid foods high in potassium (bananas, oranges, potatoes) until your next blood test. Your cardiology appointment should be scheduled soon — please call us if you haven''t heard within 2 weeks. Next visit: March 2026.',
     'info', 'patient_visible', '2026-02-01 10:00:00+08');

  -- Highlights (using jsonb_build_object for proper type)
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

END;
$$ LANGUAGE plpgsql;
