'use client';

import React from 'react';
import { ShieldCheck, ShieldAlert, ShieldQuestion, Info } from 'lucide-react';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { CONFIDENCE_BANDS, type ConfidenceBand, type SafetyMetadata } from '@/lib/types';

/**
 * Confidence badge — progressive trust disclosure.
 *
 * A self-reported model confidence is decoration. This renders a *measured*
 * quantity and, on hover, shows exactly how it was computed:
 *
 *   0.50 x ensemble agreement + 0.35 x extraction verification + 0.15 x rule support
 *
 * Three deliberate choices:
 *
 * 1. The numeric meaning of the band is shown, not just the word. A clinician
 *    seeing "medium" should be able to learn it means 0.60-0.84 without asking.
 * 2. Confidence is never derived from importance_score. They measure different
 *    things — queue position versus reliability — and showing one as the other
 *    is what makes a trust signal meaningless.
 * 3. An abstained item is labelled as withheld-pending-review rather than shown
 *    as low confidence, because the system did not make a claim at all.
 */

interface ConfidenceBadgeProps {
  score?: number | null;
  band?: ConfidenceBand | null;
  abstained?: boolean;
  metadata?: SafetyMetadata | null;
  /** Critical finding surfaced despite low confidence — flagged, not hidden. */
  unverified?: boolean;
  compact?: boolean;
}

const bandStyles: Record<ConfidenceBand, { cls: string; Icon: React.ElementType }> = {
  high: { cls: 'border-green-200/80 bg-green-50 text-green-700', Icon: ShieldCheck },
  medium: { cls: 'border-amber-200/80 bg-amber-50 text-amber-700', Icon: ShieldQuestion },
  low: { cls: 'border-red-200/80 bg-red-50 text-red-700', Icon: ShieldAlert },
};

export function ConfidenceBadge({
  score,
  band,
  abstained = false,
  metadata,
  unverified = false,
  compact = false,
}: ConfidenceBadgeProps) {
  // No assessment recorded: say so plainly rather than implying a value.
  if (score === null || score === undefined || !band) {
    if (!abstained) return null;
  }

  const resolved: ConfidenceBand = band ?? 'low';
  const { cls, Icon } = bandStyles[resolved];
  const pct = typeof score === 'number' ? Math.round(score * 100) : null;
  const components = metadata?.confidence_components;

  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${cls}`}
            aria-label={`Confidence ${resolved}${pct !== null ? `, ${pct} percent` : ''}`}
          >
            <Icon className="h-3 w-3 shrink-0" />
            {!compact && <span className="capitalize">{resolved}</span>}
            {pct !== null && <span className="tabular-nums">{pct}%</span>}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs">
          <div className="space-y-1.5">
            <p className="font-medium">
              Confidence: {resolved} {pct !== null && `(${(score as number).toFixed(2)})`}
            </p>
            <p className="text-xs opacity-80">{CONFIDENCE_BANDS[resolved]}</p>

            {components && (
              <div className="border-t border-white/15 pt-1.5 text-xs opacity-80">
                <p className="mb-0.5">Measured, not self-reported:</p>
                <ul className="space-y-0.5 tabular-nums">
                  <li>agreement across samples · {components.agreement?.toFixed(2) ?? '—'}</li>
                  <li>quote verified in source · {components.verification?.toFixed(2) ?? '—'}</li>
                  <li>deterministic rule support · {components.rule_support?.toFixed(2) ?? '—'}</li>
                </ul>
              </div>
            )}

            {metadata?.extraction_verdict && (
              <p className="text-xs opacity-80">
                Source match: {metadata.extraction_verdict}
              </p>
            )}

            {unverified && (
              <p className="border-t border-white/15 pt-1.5 text-xs text-amber-200">
                Shown despite low confidence because the finding is critical.
                Withholding it would be the worse failure. Verify before acting.
              </p>
            )}
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/**
 * Marker for a claim the system declined to make.
 *
 * Abstention is a result, not an absence: below the 0.60 threshold the system
 * routes the item to manual review instead of guessing.
 */
export function AbstainedBadge({ reason }: { reason?: string }) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex items-center gap-1 rounded-md border border-neutral-200 bg-neutral-50 px-1.5 py-0.5 text-[10px] font-medium text-neutral-600">
            <Info className="h-3 w-3 shrink-0" />
            Abstained
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs">
          <p className="font-medium">Withheld pending review</p>
          <p className="mt-1 text-xs opacity-80">
            {reason ??
              'Confidence fell below the 0.60 threshold, so no claim was made. Sent to manual review rather than shown as a finding.'}
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
