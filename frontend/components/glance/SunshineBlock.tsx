'use client';

import React from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardList,
  Eye,
  ShieldCheck,
  Sparkles,
  UserCheck,
} from 'lucide-react';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { Card, CardContent } from '@/components/ui/card';
import type { GlanceCache, Highlight, TimelineEntry, UserRole } from '@/lib/types';

/**
 * Sunshine & Transparency Disclosure Block.
 *
 * Sits above everything else on the care note and answers, in one glance, three
 * questions a clinician should never have to dig for:
 *
 *   1. What needs doing?      open actions and critical flags
 *   2. How much of this is
 *      the AI's opinion?      how many claims are AI-derived, at what confidence,
 *                             and how many the system declined to make
 *   3. Is it auditable?       PHI redaction state, provenance coverage, and who
 *                             authored what — system versus human
 *
 * The design principle is that aggregate trust is itself a number that can be
 * wrong, so this block never invents one. Every figure here is counted from
 * data actually present on the highlights — if no safety assessment was
 * recorded, it says "not assessed" rather than showing a comforting default.
 */

interface SunshineBlockProps {
  glanceCache: GlanceCache;
  highlights: Highlight[];
  entries: TimelineEntry[];
  userRole: UserRole;
  conflictCount?: number;
  onReviewConflicts?: () => void;
  onOpenAction?: (index: number) => void;
}

function Stat({
  icon: Icon,
  label,
  value,
  tone = 'neutral',
  tooltip,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  tone?: 'neutral' | 'good' | 'warn' | 'bad';
  tooltip: string;
}) {
  const tones = {
    neutral: 'text-muted-foreground',
    good: 'text-emerald-600',
    warn: 'text-amber-600',
    bad: 'text-red-600',
  };
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="flex items-start gap-2 rounded-md px-2 py-1.5 hover:bg-secondary/40 cursor-help">
            <Icon className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${tones[tone]}`} />
            <div className="min-w-0">
              <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
              <p className={`text-xs font-medium tabular-nums ${tones[tone]}`}>{value}</p>
            </div>
          </div>
        </TooltipTrigger>
        <TooltipContent side="bottom" className="max-w-xs">
          <p className="text-xs">{tooltip}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export function SunshineBlock({
  glanceCache,
  highlights,
  entries,
  userRole,
  conflictCount = 0,
  onReviewConflicts,
  onOpenAction,
}: SunshineBlockProps) {
  // ---- 1. Open actions and critical flags -------------------------------
  const items = glanceCache.top_items ?? [];
  const openActions = items.filter(
    (i) => i.type === 'action' && i.status !== 'resolved'
  );
  const criticalFlags = items.filter(
    (i) => i.risk_level === 'critical' || i.risk_level === 'high'
  );

  // ---- 2. AI disclosure --------------------------------------------------
  const aiHighlights = highlights.filter((h) => h.created_by === 'system');
  const assessed = aiHighlights.filter(
    (h) => typeof h.confidence_score === 'number'
  );
  const abstained = highlights.filter((h) => h.abstained).length;
  const floored = highlights.filter(
    (h) => h.risk_floor && h.model_risk && h.risk_floor !== h.model_risk
  ).length;
  const meanConfidence =
    assessed.length > 0
      ? assessed.reduce((sum, h) => sum + (h.confidence_score ?? 0), 0) / assessed.length
      : null;

  // ---- 3. Auditability ---------------------------------------------------
  const withProvenance = highlights.filter((h) => h.provenance_pointer != null).length;
  const provenanceCoverage =
    highlights.length > 0 ? Math.round((withProvenance / highlights.length) * 100) : null;
  const systemAuthored = entries.filter((e) => e.author_role === 'system').length;
  const humanAuthored = entries.length - systemAuthored;

  const patientView = userRole === 'patient';

  return (
    <Card className="border-primary/20 bg-gradient-to-br from-primary/[0.04] to-transparent">
      <CardContent className="pt-4 pb-3 space-y-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary shrink-0" />
          <h2 className="text-sm font-semibold">
            {patientView ? 'About your care summary' : 'Sunshine disclosure'}
          </h2>
          <span className="ml-auto text-[10px] text-muted-foreground">
            {patientView
              ? 'What your care team reviewed'
              : 'Full sunlight on AI processing and auditability'}
          </span>
        </div>

        {/* ---- Open actions & critical flags ---- */}
        {(openActions.length > 0 || criticalFlags.length > 0 || conflictCount > 0) && (
          <div className="space-y-1.5">
            {conflictCount > 0 && (
              <button
                type="button"
                onClick={onReviewConflicts}
                className="flex w-full items-center gap-2 rounded-md border border-red-200 bg-red-50 px-2.5 py-1.5 text-left text-xs font-medium text-red-700 transition-colors hover:bg-red-100"
              >
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                {conflictCount} unresolved {conflictCount === 1 ? 'contradiction' : 'contradictions'}
                <span className="ml-auto opacity-70">Review →</span>
              </button>
            )}

            {openActions.map((item, i) => (
              <button
                key={`action-${i}`}
                type="button"
                onClick={() => onOpenAction?.(i)}
                className="flex w-full items-start gap-2 rounded-md border border-amber-200/70 bg-amber-50/70 px-2.5 py-1.5 text-left text-xs text-amber-800 transition-colors hover:bg-amber-100/70"
              >
                <ClipboardList className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span className="flex-1">{item.text}</span>
                {item.status && (
                  <span className="text-[10px] uppercase tracking-wide opacity-70">
                    {item.status}
                  </span>
                )}
              </button>
            ))}

            {criticalFlags.map((item, i) => (
              <div
                key={`flag-${i}`}
                className="flex items-start gap-2 rounded-md border border-red-200/70 bg-red-50/70 px-2.5 py-1.5 text-xs text-red-800"
              >
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span className="flex-1">{item.text}</span>
                <span className="text-[10px] font-semibold uppercase tracking-wide">
                  {item.risk_level}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* ---- Trust, calibration and audit ---- */}
        {!patientView && (
          <div className="grid grid-cols-2 gap-1 border-t border-border pt-2 sm:grid-cols-3">
            <Stat
              icon={Sparkles}
              label="AI-derived"
              value={`${aiHighlights.length} of ${highlights.length}`}
              tooltip={
                `${aiHighlights.length} of ${highlights.length} highlights were generated by the ` +
                `AI scribe; the rest are clinician-created. Every AI claim must be a verbatim ` +
                `span of a source entry — paraphrase is rejected before storage.`
              }
            />
            <Stat
              icon={ShieldCheck}
              label="Mean confidence"
              value={meanConfidence !== null ? meanConfidence.toFixed(2) : 'not assessed'}
              tone={
                meanConfidence === null ? 'neutral' : meanConfidence >= 0.85 ? 'good' : 'warn'
              }
              tooltip={
                'Measured, never self-reported: 0.50 x agreement across samples + 0.35 x ' +
                'extraction verification + 0.15 x deterministic rule support. High is >= 0.85, ' +
                'medium 0.60-0.84. Below 0.60 the system abstains. ' +
                (meanConfidence === null
                  ? 'No assessment is recorded on these highlights, so no score is shown rather than a default.'
                  : `Averaged over ${assessed.length} assessed highlight(s).`)
              }
            />
            <Stat
              icon={Eye}
              label="Abstained"
              value={String(abstained)}
              tone={abstained > 0 ? 'warn' : 'neutral'}
              tooltip={
                'Claims the system declined to make because confidence fell below 0.60. ' +
                'These are routed to manual review rather than guessed. Critical findings are ' +
                'the exception: they surface flagged, because silently withholding one is the ' +
                'worse failure.'
              }
            />
            <Stat
              icon={AlertTriangle}
              label="Rule floors applied"
              value={String(floored)}
              tone={floored > 0 ? 'warn' : 'neutral'}
              tooltip={
                'Highlights where a deterministic rule raised the risk above what the model ' +
                'proposed. final_risk = max(floor, model_proposal) — the model can raise risk ' +
                'but never lower it, so drift between runs cannot bury a critical finding.'
              }
            />
            <Stat
              icon={CheckCircle2}
              label="Provenance"
              value={provenanceCoverage !== null ? `${provenanceCoverage}%` : '—'}
              tone={provenanceCoverage === 100 ? 'good' : provenanceCoverage === null ? 'neutral' : 'warn'}
              tooltip={
                `${withProvenance} of ${highlights.length} highlights carry a provenance pointer ` +
                'resolving to a source entry and character span. Click any highlight to flash ' +
                'the exact words it came from.'
              }
            />
            <Stat
              icon={UserCheck}
              label="Authorship"
              value={`${humanAuthored} human · ${systemAuthored} system`}
              tooltip={
                'AI-scribed entries are written with author_role = system and author_id = NULL, ' +
                'so they are never attributable to a clinician who did not write them. ' +
                'Patients cannot read them at all.'
              }
            />
          </div>
        )}

        {/* ---- PHI posture ---- */}
        <div className="flex items-start gap-2 border-t border-border pt-2 text-[11px] text-muted-foreground">
          <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" />
          <p>
            {patientView ? (
              <>
                Anything written for you is checked against your record before it is sent, and a
                named clinician approves it. Nothing is sent to you on the AI&apos;s say-so alone.
              </>
            ) : (
              <>
                <span className="font-medium text-foreground">PHI redacted before LLM.</span>{' '}
                Names, NRIC/FIN (incl. M series), SG phone numbers and MRNs are stripped in{' '}
                <code className="rounded bg-secondary px-1 py-0.5 text-[10px]">
                  services/redaction.py
                </code>{' '}
                before any Groq call. Logs record entity counts, never PHI. Patient-facing
                messages additionally require grounding checks and named clinician approval.
              </>
            )}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
