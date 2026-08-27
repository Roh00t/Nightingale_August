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

  // PATIENT DATA ISOLATION
  //
  // The clinical risk assessment — severity chips (CRITICAL/HIGH), confidence,
  // and unresolved clinical actions like "eGFR declining 62 -> 45" — lives in
  // care_note_assessments, which has NO patient policy. A patient gets zero
  // rows from it whatever route they take, including a direct PostgREST call
  // with their own JWT.
  //
  // That separation is the actual control. Stripping fields in a component only
  // hides them from the page: RLS is row-level, not column-level, so anything
  // left inside the patient-readable care_notes row stays readable by the
  // patient who owns it.
  //
  // Here the two are recomposed for the care team, so every downstream
  // component keeps the glance_cache shape it already expects.
  const careNote = (data as CareNote | null) ?? null;
  const isCareTeam =
    viewerRole === 'clinician' || viewerRole === 'staff' || viewerRole === 'admin';

  let safeCareNote = careNote;

  if (careNote && isCareTeam) {
    // RLS is what permits this read; the role check avoids a pointless query.
    const { data: assessment } = await supabase
      .from('care_note_assessments')
      .select('assessment')
      .eq('care_note_id', careNote.id)
      .maybeSingle();

    const internal = (assessment?.assessment ?? {}) as {
      top_items?: unknown[];
      changes_since_last_visit?: unknown[];
    };

    safeCareNote = {
      ...careNote,
      glance_cache: {
        ...careNote.glance_cache,
        top_items: (internal.top_items ?? []) as CareNote['glance_cache']['top_items'],
        changes_since_last_visit: (internal.changes_since_last_visit ??
          []) as CareNote['glance_cache']['changes_since_last_visit'],
      },
    };
  } else if (careNote) {
    // Patients: the assessment was never fetched, so there is nothing to strip.
    // These stay empty so components render the same way for every role.
    safeCareNote = {
      ...careNote,
      glance_cache: {
        ...careNote.glance_cache,
        top_items: [],
        changes_since_last_visit: [],
      },
    };
  }

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
