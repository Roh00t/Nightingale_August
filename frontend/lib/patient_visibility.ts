import type { TimelineEntry } from '@/lib/types';

/**
 * What a patient is allowed to be shown.
 *
 * This lives in its own module rather than inside `PatientWorkspace` because it
 * is a clinical rule, not a rendering detail, and a rule buried at line 1230 of
 * a 1300-line component is a rule nobody reviews.
 *
 * ---------------------------------------------------------------------------
 * THIS IS NOT A SECURITY CONTROL. Read this before relying on it.
 *
 * The boundary that actually stops a patient reading unapproved clinical text
 * is in Postgres: both care-team INSERT policies on `timeline_entries` carry
 * `AND visibility = 'internal'`, so no user JWT can create a patient-visible
 * row at all — only the gated service-role write path can, and that path runs
 * `screen_patient_draft` first.
 *
 * This function stops unapproved rows being *rendered*. It is not what stops
 * them being *created*, and a client-side predicate could never be.
 * ---------------------------------------------------------------------------
 *
 * Why it exists anyway: `visibility === 'patient_visible'` was necessary and
 * never sufficient. Rows written before the maker-checker gate shipped (31 Aug
 * 2026) carry no approval verdict, and one of them reached a patient's screen
 * reading "Lisinopril to 10 0000000mg daily" — a fabricated dose no human ever
 * signed off, rendered in the same card, in the same type, as a real
 * instruction. Nothing about the row said it was unvetted.
 *
 * So the rule keys on the approval record, not on the visibility flag.
 */
export function isApprovedForPatient(entry: TimelineEntry): boolean {
  const md = (entry.metadata ?? {}) as Record<string, unknown>;

  // Retraction notices are system-authored corrections, not drafted messages,
  // so they carry no verdict by construction. Filtering them out would mean a
  // patient is never told a message was withdrawn — worse than the bug this
  // function exists to fix.
  if (md.kind === 'retraction') return true;

  // A retracted entry stays visible, struck through, with its reason attached.
  // The patient has already read it; hiding it now leaves them holding a
  // correction with nothing to attach it to. This is the one case where an
  // unapproved row is deliberately rendered — and it renders wearing a
  // [WITHDRAWN BY CARE TEAM] badge, which is the correction working, not a leak.
  if (entry.is_retracted) return true;

  return md.patient_gate_verdict === 'passed';
}
