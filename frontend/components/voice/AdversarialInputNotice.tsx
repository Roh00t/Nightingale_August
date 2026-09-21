/**
 * Tells a clinician that the SOURCE INPUT contained command-like phrasing.
 *
 * WHAT THE FLAG MEANS, exactly. Someone whose words reached the model said or
 * wrote something shaped like an instruction to it. It does NOT mean the
 * summary is wrong, it does NOT mean the model obeyed, and it does NOT mean
 * anything was blocked — the note was produced and is shown in full. Injection
 * and hallucination are different failures with different remedies, so this
 * notice never uses the word "hallucination": a clinician sent looking for
 * invented content when the real risk is an injected directive is looking for
 * the wrong thing.
 *
 * WHY IT IS NOT A BLOCK. A consult cannot be re-recorded. Refusing to produce a
 * note because a phrase matched a heuristic would lose the clinical content of
 * a real consultation, so the generation proceeds and a human is told to look
 * harder. The detector is advisory by construction.
 *
 * WHY IT IS NOT SHOWN TO PATIENTS. "Anomaly detected" is not actionable for a
 * patient, and where a patient's own recording carries the phrasing they are
 * the likely author. Gating happens at each call site, which is where the
 * viewer's role lives.
 *
 * WHY `source` EXISTS. The same flag is set on three paths. /transcribe and
 * /scribe scan speech; /summarize scans typed timeline entries and the
 * caller-supplied patient_context. Saying "in this recording" over a typed
 * entry would send a clinician hunting for audio that does not exist, so the
 * one sentence that names the medium varies and nothing else does.
 *
 * Presentational only — no state, no fetch, no effect. It renders next to a Yjs
 * editor in some call sites and must never touch the document.
 */
export type AdversarialInputSource = 'audio' | 'text' | 'unknown';

export function AdversarialInputNotice({
  source = 'audio',
}: {
  source?: AdversarialInputSource;
}) {
  const origin =
    source === 'audio'
      ? 'Someone in this recording said something shaped like an instruction to the AI.'
      : source === 'text'
        ? 'Text in this note was written in a form shaped like an instruction to the AI.'
        : 'Something in the source material was shaped like an instruction to the AI.';

  return (
    <div
      role="alert"
      data-testid="adversarial-input-notice"
      className="rounded-md border-2 border-amber-600 bg-amber-100 px-3 py-2 dark:bg-amber-950/50"
    >
      <p className="text-sm font-bold uppercase tracking-wide text-amber-900 dark:text-amber-300">
        Review Carefully — Command-Like Phrasing In Source Input
      </p>
      <p className="mt-1 text-sm font-medium leading-relaxed text-amber-900 dark:text-amber-200">
        {origin} The summary was still produced and the phrasing was contained,
        not followed. Check that the note reflects what was clinically observed
        and nothing more.
      </p>
    </div>
  );
}

/**
 * Which medium produced the flag, from a timeline entry's stored metadata.
 *
 * `injection_signal_fields` is written by services/prompt_integrity.py:
 * "transcript" from the audio paths, "entry[N]" / "patient_context" from
 * /summarize. An entry flagged before this field existed returns 'unknown'
 * rather than guessing a medium — the generic sentence is accurate for both.
 */
export function inputSourceFromMetadata(
  metadata: Record<string, unknown> | null | undefined,
): AdversarialInputSource {
  const fields = metadata?.injection_signal_fields;
  if (!Array.isArray(fields) || fields.length === 0) return 'unknown';
  if (fields.includes('transcript')) return 'audio';
  return 'text';
}
