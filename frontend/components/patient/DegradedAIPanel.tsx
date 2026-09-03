'use client';

import { AITimeoutFallback, OfflineModeBadge } from '@/components/ui/AITimeoutFallback';
import type { AIFailureKind } from '@/lib/ai_client';
import type { OfflineFinding } from '@/lib/offline_summary';

interface DegradedAIPanelProps {
  kind: AIFailureKind;
  findings: OfflineFinding[];
  /**
   * `offlineCoverageNote(entries)`, computed at the CALL SITE.
   *
   * Deliberately a string rather than having this component import and call it:
   * that function scans every timeline entry and is not memoised (unlike
   * `offlineFindings`). Computing it here would hide a full scan inside a
   * render and change how often it runs.
   */
  coverageNote: string;
  onRetry: () => void;
}

/**
 * What the clinician sees when the contradiction check could not run.
 *
 * Returns a Fragment, NOT a wrapping <div>. The two blocks are siblings inside
 * the left column's `space-y-3` container; wrapping them would collapse the gap
 * between them and add an unspaced group. This is a pure move — the rendered
 * DOM must be byte-identical to what PatientWorkspace produced inline.
 *
 * This panel is never placed inside a collapsible section. An outage that can be
 * folded away is an outage nobody sees (guardrails.md UI-1).
 */
export function DegradedAIPanel({ kind, findings, coverageNote, onRetry }: DegradedAIPanelProps) {
  return (
    <>
      <AITimeoutFallback kind={kind} onRetry={onRetry} />

      {/* Rule-derived findings, shown ONLY while the AI is unavailable. Every
          line is a threshold applied to a value already in the record — no
          model, no inference.

          Why this rather than an empty panel: an empty "critical flags" list
          does not read as "we could not check", it reads as "there are none".
          A clinician who trusts that panel would be handed a false negative
          by an infrastructure failure nobody told them about. */}
      <div className="rounded-lg border border-border bg-secondary/30 p-3 space-y-2">
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs font-semibold">Findings while AI is unavailable</p>
          <OfflineModeBadge />
        </div>
        {findings.length > 0 ? (
          <ul className="space-y-1.5">
            {findings.map((f) => (
              <li key={f.text} className="text-xs">
                <span className={f.risk === 'critical' || f.risk === 'high'
                  ? 'font-semibold text-destructive' : 'text-foreground'}>
                  {f.text}
                </span>
                <span className="block text-[11px] text-muted-foreground">{f.basis}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">
            No threshold rule matched a stored value.
          </p>
        )}
        <p className="text-[11px] text-muted-foreground leading-relaxed border-t border-border pt-1.5">
          {coverageNote}
        </p>
      </div>
    </>
  );
}
