'use client';

import React from 'react';
import { AlertTriangle, GitCompareArrows } from 'lucide-react';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import type { ClinicalConflict, ConflictClaim } from '@/lib/types';

/**
 * Clinical contradiction badge.
 *
 * Human-human contradictions are real: a clinician writes 10mg, a nurse records
 * 100mg. The system's job here is to surface the delta, not to arbitrate it. It
 * has no basis to decide which professional is right, and picking one would
 * manufacture false certainty about a dosing decision.
 *
 * So the badge shows both claims side by side, verbatim, with who said what and
 * when — and says explicitly that a clinician must resolve it. Allergy
 * contradictions rank above dosage because they are the ones that kill people.
 */

const classLabels: Record<ClinicalConflict['conflict_class'], string> = {
  allergy: 'Allergy conflict',
  dosage: 'Dosage conflict',
  medication: 'Medication conflict',
  vital: 'Vitals conflict',
};

const severityStyles: Record<ClinicalConflict['severity'], string> = {
  critical: 'border-red-300 bg-red-50 text-red-700',
  high: 'border-amber-300 bg-amber-50 text-amber-700',
};

function ClaimRow({ claim }: { claim: ConflictClaim }) {
  return (
    <div className="rounded border border-white/15 bg-white/5 p-1.5">
      <div className="mb-0.5 flex items-baseline justify-between gap-2">
        <span className="text-[10px] font-medium uppercase tracking-wide opacity-80">
          {claim.author_role}
        </span>
        <span className="font-mono text-xs font-semibold tabular-nums">{claim.value}</span>
      </div>
      {/* Verbatim. A paraphrase here would defeat the point of showing it. */}
      <p className="text-[11px] leading-snug opacity-75">&ldquo;{claim.quote}&rdquo;</p>
    </div>
  );
}

export function ConflictBadge({
  conflict,
  onClick,
}: {
  conflict: ClinicalConflict;
  onClick?: () => void;
}) {
  const label = classLabels[conflict.conflict_class];
  const values = conflict.claims.map((c) => c.value);

  return (
    <TooltipProvider delayDuration={100}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={onClick}
            className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium transition-colors hover:brightness-95 ${severityStyles[conflict.severity]}`}
            aria-label={`${label} on ${conflict.entity}: ${values.join(' versus ')}`}
          >
            <AlertTriangle className="h-3 w-3 shrink-0" />
            <span>{label}</span>
            {values.length === 2 && (
              <span className="font-mono tabular-nums opacity-90">
                {values[0]} ≠ {values[1]}
              </span>
            )}
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-sm">
          <div className="space-y-2">
            <p className="flex items-center gap-1.5 font-medium">
              <GitCompareArrows className="h-3.5 w-3.5" />
              {label} — {conflict.entity.replace(/^allergy:/, '')}
            </p>

            <div className="space-y-1.5">
              {conflict.claims.map((claim, i) => (
                <ClaimRow key={`${claim.entry_id}-${i}`} claim={claim} />
              ))}
            </div>

            <p className="border-t border-white/15 pt-1.5 text-xs opacity-80">
              These notes disagree. The system does not choose between them — a
              clinician must resolve this. Click to open both sources in the timeline.
            </p>
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/** Summary chip for the glance card when a note has unresolved contradictions. */
export function ConflictSummaryBadge({
  count,
  hasCritical,
  onClick,
}: {
  count: number;
  hasCritical: boolean;
  onClick?: () => void;
}) {
  if (count <= 0) return null;
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex w-full items-center justify-between gap-2 rounded-lg border px-2.5 py-2 text-xs font-medium transition-colors hover:brightness-95 ${
        hasCritical ? severityStyles.critical : severityStyles.high
      }`}
    >
      <span className="flex items-center gap-1.5">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
        {count} unresolved {count === 1 ? 'contradiction' : 'contradictions'}
      </span>
      <span className="opacity-70">Review →</span>
    </button>
  );
}
