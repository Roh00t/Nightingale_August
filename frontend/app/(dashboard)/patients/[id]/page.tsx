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

  // Single indexed read. Selecting explicit columns rather than * keeps the
  // yjs_state bytea column — which can be large — off this path entirely.
  const { data, error } = await supabase
    .from('care_notes')
    .select('id, patient_id, clinic_id, glance_cache, glance_cache_updated_at, created_at, updated_at')
    .eq('patient_id', patientId)
    .maybeSingle();

  const elapsed = performance.now() - started;

  // Server-timing log for the P95 evidence in the technical brief. Records
  // duration and outcome only — never note content.
  console.log(
    `[glance] care_note read patient=${patientId} ${elapsed.toFixed(1)}ms ` +
      `${error ? `error=${error.code}` : data ? 'hit' : 'miss'}`
  );

  return (
    <PatientWorkspace
      patientId={patientId}
      initialCareNote={(data as CareNote | null) ?? null}
    />
  );
}
