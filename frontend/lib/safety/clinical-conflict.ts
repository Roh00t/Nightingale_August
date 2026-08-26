import type { ClinicalConflict, ConflictClaim, TimelineEntry } from '@/lib/types';

/**
 * Client-side clinical contradiction detection.
 *
 * A TypeScript port of `ai-service/services/safety/clinical_conflict.py`, kept
 * deliberately in lockstep with it — same patterns, same exclusions, same
 * ordering. The Python module is the tested reference (see
 * `tests/test_clinical_safety.py::TestClinicalConflictDetection`); this exists
 * so the UI can flag a contradiction the moment a timeline loads, without a
 * round trip to the AI service.
 *
 * Deterministic regex, no model. It cannot hallucinate a contradiction that is
 * not in the text, and it produces the same answer every run.
 *
 * The system surfaces the delta and never arbitrates. If a clinician wrote 10mg
 * and a nurse recorded 100mg, there is no basis to decide which is right, and
 * choosing would manufacture false certainty about a dosing decision.
 */

const UNIT = '(?:mg|mcg|µg|g|ml|units?|iu)';

const DOSE = new RegExp(
  `\\b([A-Z][a-z]{3,}(?:ol|in|ide|ine|pril|artan|statin|azole|mycin|cillin|formin|pam|done))\\b` +
    `\\s*(?:at|to|-)?\\s*(\\d+(?:\\.\\d+)?)\\s*(${UNIT})\\b`,
  'gi',
);

const ALLERGY =
  /\ballerg(?:y|ic|ies)\s+(?:to\s+)?([A-Za-z][A-Za-z\- ]{2,30}?)\b(?=[.,;)]|\s+(?:and|with|but)\b|$)/gi;

const NO_ALLERGY =
  /\b(?:no\s+known\s+(?:drug\s+)?allergies|not\s+allergic\s+to\s+([A-Za-z][A-Za-z\- ]{2,30}?)\b)/gi;

interface Assertion extends ConflictClaim {
  entity: string;
  conflictClass: ClinicalConflict['conflict_class'];
}

function quoteAround(text: string, start: number, end: number, pad = 60): string {
  return text.slice(Math.max(0, start - pad), Math.min(text.length, end + pad)).trim();
}

function extractAssertions(entry: TimelineEntry): Assertion[] {
  const text = entry.content_text ?? '';
  if (!text) return [];

  const base = {
    author_role: entry.author_role ?? 'unknown',
    author_id: entry.author_id ?? null,
    entry_id: entry.id,
    timestamp: entry.created_at,
  };
  const found: Assertion[] = [];

  for (const m of text.matchAll(DOSE)) {
    found.push({
      ...base,
      entity: m[1].toLowerCase(),
      value: `${m[2]}${m[3].toLowerCase()}`,
      conflictClass: 'dosage',
      quote: quoteAround(text, m.index ?? 0, (m.index ?? 0) + m[0].length),
    });
  }

  const deniedSpans: Array<[number, number]> = [];
  for (const m of text.matchAll(NO_ALLERGY)) {
    const start = m.index ?? 0;
    deniedSpans.push([start, start + m[0].length]);
    found.push({
      ...base,
      entity: `allergy:${(m[1] ?? 'any').trim().toLowerCase()}`,
      value: 'none',
      conflictClass: 'allergy',
      quote: quoteAround(text, start, start + m[0].length),
    });
  }

  for (const m of text.matchAll(ALLERGY)) {
    const start = m.index ?? 0;
    // Skip a positive match sitting inside an explicit denial.
    if (deniedSpans.some(([s, e]) => s <= start && start < e)) continue;
    found.push({
      ...base,
      entity: `allergy:${m[1].trim().toLowerCase()}`,
      value: 'present',
      conflictClass: 'allergy',
      quote: quoteAround(text, start, start + m[0].length),
    });
  }

  return found;
}

const CLASS_ORDER: Record<ClinicalConflict['conflict_class'], number> = {
  allergy: 0,
  dosage: 1,
  medication: 2,
  vital: 3,
};

export function detectClinicalConflicts(entries: TimelineEntry[]): ClinicalConflict[] {
  const assertions = entries.flatMap(extractAssertions);

  const grouped = new Map<string, Assertion[]>();
  for (const a of assertions) {
    const key = `${a.entity}|${a.conflictClass}`;
    grouped.set(key, [...(grouped.get(key) ?? []), a]);
  }

  const conflicts: ClinicalConflict[] = [];
  for (const group of grouped.values()) {
    const values = new Set(group.map((a) => a.value.toLowerCase().replace(/\s+/g, '')));
    // Same-author revision over time is a correction, not a disagreement.
    const authors = new Set(group.map((a) => a.author_id));
    if (values.size < 2 || authors.size < 2) continue;

    conflicts.push({
      conflict_class: group[0].conflictClass,
      entity: group[0].entity,
      severity: group[0].conflictClass === 'allergy' ? 'critical' : 'high',
      requires_human_resolution: true,
      claims: [...group]
        .sort((a, b) => String(a.timestamp ?? '').localeCompare(String(b.timestamp ?? '')))
        .map(({ author_role, author_id, entry_id, value, quote, timestamp }) => ({
          author_role,
          author_id,
          entry_id,
          value,
          quote,
          timestamp,
        })),
    });
  }

  // Allergy contradictions first — they are the ones that kill people.
  return conflicts.sort(
    (a, b) =>
      CLASS_ORDER[a.conflict_class] - CLASS_ORDER[b.conflict_class] ||
      a.entity.localeCompare(b.entity),
  );
}
