-- Add 'patient_message' to the timeline_entries entry_type CHECK constraint
ALTER TABLE timeline_entries DROP CONSTRAINT IF EXISTS timeline_entries_entry_type_check;
ALTER TABLE timeline_entries ADD CONSTRAINT timeline_entries_entry_type_check
  CHECK (entry_type IN (
    'manual_note', 'ai_doctor_consult_summary', 'ai_nurse_consult_summary',
    'ai_patient_session_summary', 'instruction', 'admin', 'system_event',
    'patient_message'
  ));
