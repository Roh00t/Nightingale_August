/**
 * Tells a clinician that the SOURCE AUDIO contained command-like phrasing.
 *
 * WHAT THE FLAG MEANS, exactly. Someone audible in the room said something
 * shaped like an instruction to the model. It does NOT mean the summary is
 * wrong, it does NOT mean the model obeyed, and it does NOT mean anything was
 * blocked — the note was produced and is shown in full. Injection and
 * hallucination are different failures with different remedies, so this notice
 * never uses the word "hallucination": a clinician sent looking for invented
 * content when the real risk is an injected directive is looking for the wrong
 * thing.
 *
 * WHY IT IS NOT A BLOCK. A consult cannot be re-recorded. Refusing to produce a
 * note because a phrase matched a heuristic would lose the clinical content of
 * a real consultation, so the generation proceeds and a human is told to look
 * harder. The detector is advisory by construction.
 *
 * WHY IT IS NOT SHOWN TO PATIENTS. Same reasoning as the ASR diagnostics in
 * VoiceCapture: "anomaly detected" is not actionable for a patient, and where a
 * patient's own recording carries the phrasing they are the likely author.
 * Gating happens at the call site, which is where `isPatient` lives.
 *
 * Presentational only — no state, no fetch, no effect. It renders next to a Yjs
 * editor in some call sites and must never touch the document.
 */
export function AdversarialInputNotice() {
  return (
    <div
      role="alert"
      data-testid="adversarial-input-notice"
      className="rounded-md border-2 border-amber-600 bg-amber-100 px-3 py-2 dark:bg-amber-950/50"
    >
      <p className="text-sm font-bold uppercase tracking-wide text-amber-900 dark:text-amber-300">
        Review Carefully — Command-Like Phrasing In Source Audio
      </p>
      <p className="mt-1 text-sm font-medium leading-relaxed text-amber-900 dark:text-amber-200">
        Someone in this recording said something shaped like an instruction to
        the AI. The summary was still produced and the phrasing was contained,
        not followed. Check that the note below reflects what was clinically
        observed and nothing more.
      </p>
    </div>
  );
}
