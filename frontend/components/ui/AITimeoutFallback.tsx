'use client';

import { AlertTriangle, WifiOff, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { AIFailureKind } from '@/lib/ai_client';

/**
 * What the clinician sees when the AI could not answer.
 *
 * The design constraint is that this must never look like clinical content. A
 * degraded state rendered in the same visual language as a real summary invites
 * it to be read as one — so this is explicitly an out-of-band notice, and the
 * record it sits above stays visible and authoritative.
 *
 * The wording avoids "error". A clinician mid-consult does not need to know
 * that a container was rescheduled; they need to know whether the information
 * in front of them is complete, and what to do next. So each state says what is
 * still trustworthy.
 */
export function AITimeoutFallback({
  kind,
  onRetry,
  retrying = false,
}: {
  kind: AIFailureKind;
  onRetry?: () => void;
  retrying?: boolean;
}) {
  const copy: Record<AIFailureKind, { title: string; body: string }> = {
    timeout: {
      title: 'AI service timed out — showing cached clinical record',
      body:
        'The summary did not return in time. Everything below is the stored record, ' +
        'read directly from the database — it is current and complete. Only the ' +
        'AI-derived summary is missing.',
    },
    unavailable: {
      title: 'AI service unavailable — showing cached clinical record',
      body:
        'The AI service could not be reached. The record below is unaffected: it ' +
        'comes from the database, not the model.',
    },
    unauthorized: {
      title: 'Not authorised',
      body: 'Your session may have expired. Reload the page and sign in again.',
    },
    rejected: {
      title: 'Request refused',
      body: 'The request did not pass its safety checks. See the details shown with it.',
    },
    unknown: {
      title: 'AI service error — showing cached clinical record',
      body:
        'Something went wrong generating the summary. The record below is from the ' +
        'database and is unaffected.',
    },
  };

  const { title, body } = copy[kind];
  const Icon = kind === 'unavailable' ? WifiOff : AlertTriangle;

  return (
    <div
      role="status"
      aria-live="polite"
      className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 space-y-2"
    >
      <div className="flex items-start gap-2">
        <Icon className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
        <div className="space-y-1">
          <p className="text-xs font-semibold text-amber-700 dark:text-amber-500">{title}</p>
          <p className="text-xs text-muted-foreground leading-relaxed">{body}</p>
        </div>
      </div>
      {onRetry && (
        <div className="pl-6">
          <Button size="sm" variant="outline" className="gap-1.5 h-7" onClick={onRetry} disabled={retrying}>
            <RefreshCw className={`w-3 h-3 ${retrying ? 'animate-spin' : ''}`} />
            {retrying ? 'Retrying…' : 'Try again'}
          </Button>
        </div>
      )}
    </div>
  );
}

/**
 * Badge marking content that came from deterministic rules rather than a model.
 *
 * Kept visually distinct from the trust badges used for AI output. The point of
 * offline mode is that the clinician can tell at a glance that nothing here was
 * inferred — every line is a threshold applied to a stored value — so the badge
 * has to be legible without being read as a confidence score.
 */
export function OfflineModeBadge() {
  return (
    <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 bg-amber-500/15 text-amber-700 dark:text-amber-500 text-[10px] font-medium">
      <WifiOff className="w-2.5 h-2.5" />
      Offline Mode (Rule-Derived)
    </span>
  );
}
