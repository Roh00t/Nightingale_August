'use client';

import { useEffect } from 'react';

/**
 * Route-level error boundary.
 *
 * Without this file the App Router unmounts the tree on an uncaught render error
 * and the clinician is left looking at a white screen — mid-consult, with a
 * patient in the room, and no indication of whether the record was saved.
 *
 * The design rule that applies here is the same one governing every degraded
 * state in this system: say what happened in words. A blank page is
 * indistinguishable from a slow page, a logged-out session, and a crashed
 * laptop, and a clinician cannot pick the right recovery without knowing which.
 *
 * What this deliberately does NOT do:
 *
 *   - It does not render the raw error text. A render error thrown while
 *     formatting a timeline entry can carry note content into its message, and
 *     an error screen is exactly the surface a patient standing beside the desk
 *     can read. The digest is shown instead — enough to correlate with the
 *     server log, useless to a passer-by.
 *   - It does not claim the work was saved. It cannot know, so it says so and
 *     tells the clinician how to check.
 */
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Console only, and the message only. Never the stack, which can quote the
    // values that broke it.
    console.error('[nightingale] render error:', error.message, error.digest ?? '');
  }, [error]);

  return (
    <div className="flex min-h-[60vh] items-center justify-center p-6">
      <div
        role="alert"
        className="w-full max-w-xl rounded-lg border-2 border-destructive bg-destructive/5 p-5"
      >
        <p className="text-base font-bold uppercase tracking-wide text-destructive">
          Display error — this screen could not be drawn
        </p>

        <p className="mt-3 text-sm font-medium leading-relaxed text-foreground">
          This is a problem with the interface, not with the patient record. Nothing
          shown before the error was deleted.
        </p>

        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
          <strong>If you were part-way through writing a note</strong>, that text was
          held in this page and is not recoverable after a reload. Copy anything you
          still have on screen before reloading, and re-check the record afterwards
          to confirm what was saved.
        </p>

        <div className="mt-4 flex flex-wrap gap-2">
          <button
            onClick={reset}
            className="rounded-md bg-destructive px-4 py-2 text-sm font-semibold text-white hover:opacity-90"
          >
            Reload Page
          </button>
          <button
            onClick={() => { window.location.href = '/patients'; }}
            className="rounded-md border border-border bg-background px-4 py-2 text-sm font-semibold hover:bg-secondary"
          >
            Back to patient list
          </button>
        </div>

        {error.digest && (
          <p className="mt-4 border-t border-border pt-3 font-mono text-[11px] text-muted-foreground">
            Reference for support: {error.digest}
          </p>
        )}
      </div>
    </div>
  );
}
