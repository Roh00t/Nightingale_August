/**
 * Rule-derived clinical summary for when the AI service cannot answer.
 *
 * Every line this produces is a threshold applied to a value already stored in
 * Supabase. Nothing is inferred, nothing is generated, and there is no model in
 * the path — which is exactly why it is safe to show during an outage, and why
 * it must be labelled so it is never mistaken for the AI summary it replaces.
 *
 * The alternative — showing an empty Glance View while the service is down — is
 * worse than it looks. An empty "critical flags" panel does not read as "we
 * could not check"; it reads as "there are none". A clinician who has learned
 * to trust that panel now has a false negative on a critical result, produced by
 * an infrastructure failure they were never told about.
 *
 * So the rule here is: degrade to less information, never to *reassuring*
 * information. When a value cannot be assessed, this says so rather than
 * omitting the line.
 */

import type { CareNote, TimelineEntry } from '@/lib/types';

export interface OfflineFinding {
  text: string;
  risk: 'critical' | 'high' | 'medium' | 'info';
  /** The stored value and rule this came from — shown so it can be checked. */
  basis: string;
}

/**
 * Deterministic thresholds.
 *
 * These are intentionally coarse and err toward flagging. A rule-derived
 * fallback that misses a critical value is worth less than nothing, whereas a
 * false positive during an outage costs a clinician five seconds of reading.
 */
const RULES: Array<{
  label: string;
  pattern: RegExp;
  assess: (value: number) => { risk: OfflineFinding['risk']; note: string } | null;
}> = [
  {
    label: 'Potassium',
    pattern: /\bK\+?\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:mmol\/l|meq\/l)?\b/i,
    assess: (v) =>
      v >= 6.0 ? { risk: 'critical', note: 'severe hyperkalaemia' }
      : v >= 5.5 ? { risk: 'high', note: 'hyperkalaemia' }
      : v <= 3.0 ? { risk: 'high', note: 'hypokalaemia' }
      : null,
  },
  {
    label: 'eGFR',
    pattern: /\begfr\s*[:=]?\s*(\d+(?:\.\d+)?)\b/i,
    assess: (v) =>
      v < 15 ? { risk: 'critical', note: 'kidney failure range' }
      : v < 30 ? { risk: 'high', note: 'severely reduced' }
      : v < 60 ? { risk: 'medium', note: 'moderately reduced' }
      : null,
  },
  {
    label: 'Systolic BP',
    pattern: /\b(\d{2,3})\s*\/\s*\d{2,3}\b/,
    assess: (v) =>
      v >= 180 ? { risk: 'critical', note: 'hypertensive crisis range' }
      : v >= 160 ? { risk: 'high', note: 'stage 2 hypertension' }
      : v <= 90 ? { risk: 'high', note: 'hypotension' }
      : null,
  },
];

/** Words that mean the surrounding finding did NOT happen. */
const NEGATION = /\b(no|not|denies|negative for|ruled out|without|absent)\b/i;

function isNegated(text: string, index: number): boolean {
  // Look both directions: "no chest pain" and "chest pain ruled out" are both
  // negations, and checking only backwards misses the second.
  const before = text.slice(Math.max(0, index - 40), index);
  const after = text.slice(index, index + 40);
  return NEGATION.test(before) || NEGATION.test(after);
}

export function deriveOfflineFindings(entries: TimelineEntry[]): OfflineFinding[] {
  const findings: OfflineFinding[] = [];
  const seen = new Set<string>();

  // Newest first: a stale potassium from six months ago should not outrank
  // today's, and taking only the first match per rule gives the most recent.
  const ordered = [...entries].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  );

  for (const entry of ordered) {
    const text = entry.content_text || '';
    for (const rule of RULES) {
      if (seen.has(rule.label)) continue;
      const m = rule.pattern.exec(text);
      if (!m) continue;
      if (isNegated(text, m.index)) continue;

      const value = parseFloat(m[1]);
      if (Number.isNaN(value)) continue;

      const verdict = rule.assess(value);
      seen.add(rule.label);
      if (verdict) {
        findings.push({
          text: `${rule.label} ${value} — ${verdict.note}`,
          risk: verdict.risk,
          basis: `Threshold rule on stored value "${m[0].trim()}"`,
        });
      }
    }
  }

  const order = { critical: 0, high: 1, medium: 2, info: 3 } as const;
  return findings.sort((a, b) => order[a.risk] - order[b.risk]);
}

/**
 * Open actions, read straight from the care plan.
 *
 * No inference at all — these are items a clinician already wrote down and did
 * not tick off.
 */
export function deriveOpenActions(careNote: CareNote | null): string[] {
  const items = (careNote?.glance_cache?.care_plan_items ?? []) as Array<{
    label?: string;
    completed?: boolean;
  }>;
  return items.filter((i) => i && !i.completed && i.label).map((i) => i.label as string);
}

/**
 * The honest empty state.
 *
 * Returned when there is nothing to derive, so the caller can render "not
 * assessed" rather than an empty panel that reads as "nothing found".
 */
export function offlineCoverageNote(entries: TimelineEntry[]): string {
  if (entries.length === 0) {
    return 'No stored entries to assess. This is not a clinical finding — the record could not be read.';
  }
  return (
    `Derived from ${entries.length} stored ${entries.length === 1 ? 'entry' : 'entries'} ` +
    `by threshold rules only. Values outside these rules are not assessed — ` +
    `absence of a flag here does not mean absence of a problem.`
  );
}
