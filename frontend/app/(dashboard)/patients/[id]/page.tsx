import { createClient } from '@/lib/supabase/server';
import { PatientWorkspace } from '@/components/patient/PatientWorkspace';
import type { CareNote } from '@/lib/types';

/**
 * Consult page — server-rendered Glance View.
 *
 * The brief sets a hard constraint of P95 <= 300ms warm for the consult glance
 * view. Previously this page was a client component that, after hydration, ran
 * a waterfall: getSession -> care_notes -> (entries, comments, highlights) ->
 * profiles, with a skeleton until all of it landed. The Top Card could not
 * paint until history had finished loading.
 *
 * The fix is to decouple the two reads. This server component performs ONE
 * indexed lookup against care_notes (idx_care_notes_patient) for the
 * denormalised glance_cache, and hands it to the workspace as an initial value.
 * The card is therefore present in the server HTML. Timeline, comments and
 * highlights still load client-side inside the workspace, so the volume of
 * historical data has no effect on how fast the card appears.
 *
 * Measure it with: node scripts/measure_glance.mjs
 */

export default async function PatientCareNotePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: patientId } = await params;
  const started = performance.now();

  const supabase = await createClient();

  // The caller's role decides what may leave the server. Read it here rather
  // than trusting anything the client sends.
  const { data: { user } } = await supabase.auth.getUser();
  const { data: viewer } = user
    ? await supabase.from('profiles').select('role').eq('id', user.id).maybeSingle()
    : { data: null };
  const viewerRole = viewer?.role ?? null;

  // Single indexed read. Selecting explicit columns rather than * keeps the
  // yjs_state bytea column — which can be large — off this path entirely.
  const { data, error } = await supabase
    .from('care_notes')
    .select('id, patient_id, clinic_id, glance_cache, glance_cache_updated_at, created_at, updated_at')
    .eq('patient_id', patientId)
    .maybeSingle();

  const elapsed = performance.now() - started;

  // PATIENT PRIVACY — enforced on the server, not by hiding UI.
  //
  // glance_cache.top_items holds the internal clinical assessment: risk levels,
  // severity, confidence, and open clinical actions ("eGFR declining 62 -> 45",
  // CRITICAL). RLS correctly lets a patient read their own care_notes row, so
  // the column arrives here legitimately — but that does not make its contents
  // patient-facing. Rendering it in the patient portal exposed a clinician's
  // risk judgement to the patient it was written about.
  //
  // The internal fields are removed BEFORE the payload crosses to the client,
  // so they are absent from the RSC stream and the browser bundle entirely.
  // Filtering in the component would leave the data in the page source.
  const careNote = (data as CareNote | null) ?? null;
  const safeCareNote =
    careNote && viewerRole === 'patient'
      ? {
          ...careNote,
          glance_cache: {
            // Retained: the patient's own care-plan progress.
            care_plan_score: careNote.glance_cache?.care_plan_score ?? 0,
            care_plan_items: careNote.glance_cache?.care_plan_items ?? [],
            last_visit: careNote.glance_cache?.last_visit,
            // Withheld: internal risk assessment and open clinical actions.
            top_items: [],
            changes_since_last_visit: [],
          },
        }
      : careNote;

  // Server-timing log for the P95 evidence in the technical brief. Records
  // duration and outcome only — never note content.
  console.log(
    `[glance] care_note read patient=${patientId} ${elapsed.toFixed(1)}ms ` +
      `${error ? `error=${error.code}` : data ? 'hit' : 'miss'}`
  );

  return (
    <PatientWorkspace
      patientId={patientId}
      initialCareNote={safeCareNote}
    />
  );
}
