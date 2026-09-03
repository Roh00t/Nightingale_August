import type { ClinicalConflict, GlanceCache, Highlight } from '@/lib/types';

/**
 * When the clinical summary may NOT be collapsed.
 *
 * This lives in its own module for the same reason `patient_visibility.ts` does:
 * it is a clinical rule, not a rendering detail, and a rule buried inside a
 * 1800-line component is a rule nobody reviews.
 *
 * ---------------------------------------------------------------------------
 * WHY THIS EXISTS AT ALL
 *
 * Progressive disclosure and `guardrails.md` UI-1 pull in opposite directions.
 * UI-1 says a degraded or notable state must be stated in words and never
 * implied by an absence — and a collapsed section IS an absence: a clinician
 * who sees a closed "At a Glance" concludes there was nothing worth opening.
 *
 * So collapsing is only admissible if it is impossible in the cases that matter.
 * This predicate defines those cases. When it returns true the section renders
 * with no disclosure control at all — not `<details open>`, which the clinician
 * could still close.
 *
 * This is the single point of failure for the whole collapse decision, which is
 * why it is a tested module rather than an inline expression.
 * ---------------------------------------------------------------------------
 */
export interface CriticalAlertInputs {
  highlights: Highlight[];
  conflicts: ClinicalConflict[];
  glanceCache: GlanceCache;
  /** `conflictsDegraded !== null`. An outage is itself an unacknowledged state. */
  aiDegraded: boolean;
}

export function hasActiveCriticalAlert({
  highlights,
  conflicts,
  glanceCache,
  aiDegraded,
}: CriticalAlertInputs): boolean {
  // An AI outage forces the summary open even when nothing was found — because
  // nothing was CHECKED. Collapsing here would hide "we could not check" behind
  // a closed triangle and leave "nothing found" as the natural reading. Belt and
  // braces: DegradedAIPanel also renders outside every collapsible.
  if (aiDegraded) return true;

  // "Unacknowledged" is exactly what accept/reject means on a highlight.
  // Loose `==` catches both null and undefined.
  //
  // Deliberately tighter than CriticalFlags.tsx, which renders anything with
  // `is_accepted !== false`. That is "still shown"; this is "still demanding
  // attention", and an accepted critical finding is no longer the latter.
  if (highlights.some((h) => h.risk_level === 'critical' && h.is_accepted == null)) return true;

  // ClinicalConflict has no acknowledgement field, so every element present is
  // by construction unresolved. `requires_human_resolution` is included because
  // a high-severity conflict flagged for a human is not something to fold away.
  if (hasCriticalConflict(conflicts)) return true;

  return (glanceCache.top_items ?? []).some(
    (i) => i.risk_level === 'critical' && i.status !== 'resolved',
  );
}

/**
 * Shared with the TopCard `hasCriticalConflict` prop so the two cannot drift.
 */
export function hasCriticalConflict(conflicts: ClinicalConflict[]): boolean {
  return conflicts.some((c) => c.severity === 'critical' || c.requires_human_resolution);
}

/**
 * DELIBERATELY NOT PART OF THE PREDICATE: `isSourceModified`.
 *
 * It returns true whenever `source_note_version == null`, which is every
 * highlight created before provenance tracking shipped. Including it would
 * force-open on virtually every real record, which is the same as having no
 * predicate at all — and a rule that always fires is a rule nobody reads.
 */
