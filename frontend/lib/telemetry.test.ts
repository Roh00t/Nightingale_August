import { describe, it, expect } from 'vitest';
import { buildPayload } from './telemetry';

const S = '00000000-0000-0000-0000-0000000000aa';

describe('buildPayload', () => {
  it('carries no identifying columns', () => {
    // The table has no user_id/patient_id/care_note_id, and nothing here should
    // ever try to add one. This test fails loudly if someone "enriches" the
    // payload for a dashboard join.
    const p = buildPayload(S, 'at_a_glance', 'expand') as unknown as Record<string, unknown>;
    expect(Object.keys(p).sort()).toEqual(['action', 'component_id', 'session_uuid']);
    for (const banned of ['user_id', 'patient_id', 'care_note_id', 'email', 'name']) {
      expect(p[banned]).toBeUndefined();
    }
  });

  it('does not send clinic_id or user_role — the server stamps those', () => {
    const p = buildPayload(S, 'timeline', 'view') as unknown as Record<string, unknown>;
    expect(p.clinic_id).toBeUndefined();
    expect(p.user_role).toBeUndefined();
  });

  it('clamps a backgrounded-tab dwell instead of dropping the event', () => {
    // The DB CHECK caps dwell at 1h and would reject a larger row outright.
    // Losing the event is worse than capping it: it still says "they opened it".
    expect(buildPayload(S, 'at_a_glance', 'dwell', { dwellMs: 99_999_999 }).dwell_ms)
      .toBe(3_600_000);
    expect(buildPayload(S, 'at_a_glance', 'dwell', { dwellMs: -5 }).dwell_ms).toBe(0);
  });

  it('clamps scroll depth to 0-100', () => {
    expect(buildPayload(S, 'timeline', 'scroll_depth', { valuePct: 250 }).value_pct).toBe(100);
    expect(buildPayload(S, 'timeline', 'scroll_depth', { valuePct: -3 }).value_pct).toBe(0);
  });

  it('rounds fractional values, since the columns are integers', () => {
    expect(buildPayload(S, 'at_a_glance', 'dwell', { dwellMs: 1500.7 }).dwell_ms).toBe(1501);
  });

  it('omits optional fields rather than sending null', () => {
    const p = buildPayload(S, 'sunshine', 'collapse');
    expect('dwell_ms' in p).toBe(false);
    expect('value_pct' in p).toBe(false);
  });

  it('ignores NaN and Infinity rather than sending them', () => {
    expect(buildPayload(S, 'sunshine', 'dwell', { dwellMs: NaN }).dwell_ms).toBeUndefined();
    expect(buildPayload(S, 'sunshine', 'dwell', { dwellMs: Infinity }).dwell_ms).toBeUndefined();
  });
});
