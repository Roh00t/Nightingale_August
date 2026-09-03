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

/**
 * The text a COLLAPSED section shows so that closed never reads as empty.
 *
 * guardrails.md UI-1: a degraded or notable state is stated in words, never
 * implied by an absence. A closed disclosure is an absence, so the summary line
 * has to carry the count the clinician would otherwise have to open the section
 * to learn.
 *
 * The ordering matters. "not checked" must win over any count, because a count
 * derived from a check that never ran is worse than no count — it looks like
 * evidence.
 */
export function describeGlanceLoad({
  highlights,
  conflicts,
  glanceCache,
  aiDegraded,
}: CriticalAlertInputs): string {
  if (aiDegraded) return 'not checked — AI unavailable';

  // Counted from BOTH sources, matching hasActiveCriticalAlert.
  //
  // Found by looking at the rendered page: a seeded record showed
  // "eGFR declining: 62 -> 45 over 6 months  CRITICAL" while this subtitle read
  // only "27 findings". The critical item lived in glance_cache.top_items, which
  // hasActiveCriticalAlert checks and this function did not — so the section
  // correctly force-opened while its own label failed to say why.
  //
  // Two functions disagreeing about what "critical" means is exactly the drift
  // UI-1 is meant to prevent, and it is invisible to a typecheck.
  const criticalTopItems = (glanceCache.top_items ?? []).filter(
    (i) => i.risk_level === 'critical' && i.status !== 'resolved',
  ).length;
  const critical = highlights.filter(
    (h) => h.risk_level === 'critical' && h.is_accepted !== false,
  ).length + criticalTopItems;
  const shown = highlights.filter((h) => h.is_accepted !== false).length;
  const items = (glanceCache.top_items ?? []).length;

  const parts: string[] = [];
  if (shown > 0) parts.push(`${shown} finding${shown === 1 ? '' : 's'}`);
  else if (items > 0) parts.push(`${items} item${items === 1 ? '' : 's'}`);
  if (critical > 0) parts.push(`${critical} critical`);
  if (conflicts.length > 0) {
    parts.push(`${conflicts.length} contradiction${conflicts.length === 1 ? '' : 's'}`);
  }

  // Only reachable when the check DID run and genuinely found nothing — which
  // is why the aiDegraded early return above is not optional.
  return parts.length > 0 ? parts.join(' · ') : 'nothing flagged';
}
