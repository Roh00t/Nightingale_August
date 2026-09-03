import { describe, it, expect } from 'vitest';
import { isApprovedForPatient } from './patient_visibility';
import type { TimelineEntry } from '@/lib/types';

const entry = (over: Partial<TimelineEntry>): TimelineEntry =>
  ({
    id: 'e1',
    care_note_id: 'n1',
    author_role: 'clinician',
    entry_type: 'instruction',
    content: {},
    content_text: 'text',
    visibility: 'patient_visible',
    created_at: '2026-09-03T00:00:00Z',
    metadata: {},
    ...over,
  }) as unknown as TimelineEntry;

describe('isApprovedForPatient', () => {
  it('admits an entry the gate passed', () => {
    expect(isApprovedForPatient(entry({ metadata: { patient_gate_verdict: 'passed' } }))).toBe(true);
  });

  it('refuses a patient_visible entry with no approval record', () => {
    // The regression this file exists for: entry 47aaf426, written before the
    // maker-checker gate shipped, reading "Lisinopril to 10 0000000mg daily".
    // It was patient_visible and it was rendered. Visibility is not approval.
    expect(
      isApprovedForPatient(
        entry({ content_text: 'Increase Lisinopril to 10 0000000mg daily', metadata: {} }),
      ),
    ).toBe(false);
  });

  it('refuses an entry the gate blocked', () => {
    expect(
      isApprovedForPatient(entry({ metadata: { patient_gate_verdict: 'blocked_ungrounded' } })),
    ).toBe(false);
  });

  it('refuses an entry with no metadata at all', () => {
    expect(isApprovedForPatient(entry({ metadata: undefined }))).toBe(false);
  });

  it('admits a retraction notice, which has no verdict by construction', () => {
    // If this ever returns false the patient is never told a message was
    // withdrawn — strictly worse than the bug the filter was added to fix.
    expect(isApprovedForPatient(entry({ metadata: { kind: 'retraction' } }))).toBe(true);
  });

  it('admits a retracted entry so it can be shown struck through', () => {
    // The patient already read it. Silently removing it leaves them holding a
    // correction with nothing to attach it to.
    expect(
      isApprovedForPatient(entry({ is_retracted: true, metadata: {} })),
    ).toBe(true);
  });

  it('does not treat a truthy-looking verdict as approval', () => {
    for (const v of ['passed_review', 'PASSED', 'pass', true, 1]) {
      expect(isApprovedForPatient(entry({ metadata: { patient_gate_verdict: v } }))).toBe(false);
    }
  });
});
