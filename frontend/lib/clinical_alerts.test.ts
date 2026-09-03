import { describe, it, expect } from 'vitest';
import { describeGlanceLoad, hasActiveCriticalAlert, hasCriticalConflict } from './clinical_alerts';
import type { ClinicalConflict, GlanceCache, Highlight } from '@/lib/types';

const glance = (over: Partial<GlanceCache> = {}): GlanceCache =>
  ({ top_items: [], care_plan_score: 50, last_visit: '2026-02-01', ...over }) as GlanceCache;

const hl = (over: Partial<Highlight>): Highlight =>
  ({ id: 'h1', risk_level: 'info', is_accepted: null, is_pinned: false, ...over }) as Highlight;

const conflict = (over: Partial<ClinicalConflict> = {}): ClinicalConflict =>
  ({ conflict_class: 'allergy', entity: 'penicillin', severity: 'high',
     requires_human_resolution: false, claims: [], ...over }) as ClinicalConflict;

const base = { highlights: [], conflicts: [], glanceCache: glance(), aiDegraded: false };

describe('hasActiveCriticalAlert', () => {
  it('is false on a quiet record — collapsing is allowed', () => {
    expect(hasActiveCriticalAlert(base)).toBe(false);
  });

  it('is true whenever the AI is degraded, even with nothing found', () => {
    // The whole point: nothing was found because nothing was CHECKED. If this
    // ever returns false, an outage hides behind a closed triangle and the
    // clinician reads "nothing found".
    expect(hasActiveCriticalAlert({ ...base, aiDegraded: true })).toBe(true);
  });

  describe('unacknowledged critical highlights', () => {
    it('fires on is_accepted === null', () => {
      expect(hasActiveCriticalAlert({
        ...base, highlights: [hl({ risk_level: 'critical', is_accepted: null })],
      })).toBe(true);
    });

    it('fires on is_accepted === undefined', () => {
      // Loose == is deliberate. A row that arrived without the column must not
      // read as acknowledged.
      expect(hasActiveCriticalAlert({
        ...base, highlights: [hl({ risk_level: 'critical', is_accepted: undefined as never })],
      })).toBe(true);
    });

    it('does NOT fire once a critical highlight is accepted', () => {
      expect(hasActiveCriticalAlert({
        ...base, highlights: [hl({ risk_level: 'critical', is_accepted: true })],
      })).toBe(false);
    });

    it('does NOT fire once a critical highlight is rejected', () => {
      // The pre-mortem case: keying on `is_accepted !== false` instead of
      // `== null` would make this true and force-open on rejected findings.
      expect(hasActiveCriticalAlert({
        ...base, highlights: [hl({ risk_level: 'critical', is_accepted: false })],
      })).toBe(false);
    });

    it('does not fire on an unacknowledged NON-critical highlight', () => {
      for (const risk of ['high', 'medium', 'low', 'info'] as const) {
        expect(hasActiveCriticalAlert({
          ...base, highlights: [hl({ risk_level: risk, is_accepted: null })],
        })).toBe(false);
      }
    });
  });

  describe('conflicts', () => {
    it('fires on a critical conflict', () => {
      expect(hasActiveCriticalAlert({
        ...base, conflicts: [conflict({ severity: 'critical' })],
      })).toBe(true);
    });

    it('fires on a high conflict flagged for human resolution', () => {
      expect(hasActiveCriticalAlert({
        ...base, conflicts: [conflict({ severity: 'high', requires_human_resolution: true })],
      })).toBe(true);
    });

    it('does not fire on a high conflict not needing a human', () => {
      expect(hasActiveCriticalAlert({
        ...base, conflicts: [conflict({ severity: 'high', requires_human_resolution: false })],
      })).toBe(false);
    });
  });

  describe('glance top_items', () => {
    it('fires on an unresolved critical item', () => {
      expect(hasActiveCriticalAlert({
        ...base,
        glanceCache: glance({ top_items: [{ type: 'risk', text: 'eGFR 45', risk_level: 'critical' }] }),
      })).toBe(true);
    });

    it('does not fire once that item is resolved', () => {
      expect(hasActiveCriticalAlert({
        ...base,
        glanceCache: glance({ top_items: [
          { type: 'risk', text: 'eGFR 45', risk_level: 'critical', status: 'resolved' },
        ] }),
      })).toBe(false);
    });

    it('tolerates a missing top_items array', () => {
      expect(hasActiveCriticalAlert({
        ...base, glanceCache: glance({ top_items: undefined as never }),
      })).toBe(false);
    });
  });
});

describe('hasCriticalConflict', () => {
  it('matches the predicate used inside hasActiveCriticalAlert', () => {
    const cs = [conflict({ severity: 'high', requires_human_resolution: true })];
    expect(hasCriticalConflict(cs)).toBe(true);
    expect(hasActiveCriticalAlert({ ...base, conflicts: cs })).toBe(true);
  });

  it('is false on an empty list', () => {
    expect(hasCriticalConflict([])).toBe(false);
  });
});

describe('describeGlanceLoad', () => {
  it('says "not checked" when the AI is down, never a count', () => {
    // The load-bearing case. A count derived from a check that never ran looks
    // like evidence, which is worse than no count at all.
    expect(describeGlanceLoad({
      ...base,
      aiDegraded: true,
      highlights: [hl({ risk_level: 'critical' })],
    })).toBe('not checked — AI unavailable');
  });

  it('says "nothing flagged" only when the check ran and found nothing', () => {
    expect(describeGlanceLoad(base)).toBe('nothing flagged');
  });

  it('counts findings and criticals', () => {
    expect(describeGlanceLoad({
      ...base,
      highlights: [hl({ risk_level: 'critical' }), hl({ risk_level: 'high' })],
    })).toBe('2 findings · 1 critical');
  });

  it('singularises', () => {
    expect(describeGlanceLoad({ ...base, highlights: [hl({ risk_level: 'high' })] }))
      .toBe('1 finding');
  });

  it('excludes rejected highlights from the count', () => {
    expect(describeGlanceLoad({
      ...base,
      highlights: [hl({ risk_level: 'high', is_accepted: false })],
    })).toBe('nothing flagged');
  });

  it('mentions contradictions', () => {
    expect(describeGlanceLoad({ ...base, conflicts: [conflict()] }))
      .toBe('1 contradiction');
  });

  it('falls back to top_items when there are no highlights', () => {
    expect(describeGlanceLoad({
      ...base,
      glanceCache: glance({ top_items: [{ type: 'risk', text: 'x', risk_level: 'high' }] }),
    })).toBe('1 item');
  });
});

describe('describeGlanceLoad and hasActiveCriticalAlert agree', () => {
  // Found on the rendered page: the section force-opened (correct) while its
  // subtitle said only "27 findings" (wrong). The critical lived in top_items,
  // which only one of the two functions was looking at.
  const criticalInTopItemsOnly = {
    ...base,
    highlights: [hl({ risk_level: 'info' })],
    glanceCache: glance({
      top_items: [{ type: 'risk', text: 'eGFR declining 62 -> 45', risk_level: 'critical' }],
    }),
  };

  it('force-opens when the only critical is in top_items', () => {
    expect(hasActiveCriticalAlert(criticalInTopItemsOnly)).toBe(true);
  });

  it('and SAYS SO in the subtitle', () => {
    expect(describeGlanceLoad(criticalInTopItemsOnly)).toContain('1 critical');
  });

  it('does not double-count a critical present in both sources', () => {
    // Distinct sources, so two entries is genuinely two things to look at.
    const both = {
      ...base,
      highlights: [hl({ risk_level: 'critical' })],
      glanceCache: glance({
        top_items: [{ type: 'risk', text: 'x', risk_level: 'critical' }],
      }),
    };
    expect(describeGlanceLoad(both)).toContain('2 critical');
  });

  it('ignores a resolved critical top_item, as the alert predicate does', () => {
    const resolved = {
      ...base,
      glanceCache: glance({
        top_items: [{ type: 'risk', text: 'x', risk_level: 'critical', status: 'resolved' }],
      }),
    };
    expect(hasActiveCriticalAlert(resolved)).toBe(false);
    expect(describeGlanceLoad(resolved)).not.toContain('critical');
  });
});
