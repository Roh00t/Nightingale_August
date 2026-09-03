'use client';

import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ConfidenceBadge } from '@/components/ui/confidence-badge';
import { ConflictSummaryBadge } from '@/components/ui/conflict-badge';

/** Ordinal ranking, so a floor can be compared against a model proposal. */
const RISK_ORDER: Record<string, number> = {
  info: 0, low: 1, medium: 2, high: 3, critical: 4,
};
import { Button } from '@/components/ui/button';
import { TrustBadge } from '@/components/ui/trust-badge';
import { getRiskColor } from '@/lib/utils';
import type { GlanceCache, Highlight, ChangeSinceLastVisit, CarePlanItem, UserRole } from '@/lib/types';
import { ActionItems } from './ActionItems';
import { CriticalFlags } from './CriticalFlags';
import {
  Eye,
  TrendingUp,
  TrendingDown,
  Clock,
  Plus,
  ArrowUpRight,
  Check,
  X,
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Loader2,
} from 'lucide-react';

interface TopCardProps {
  glanceCache: GlanceCache;
  highlights: Highlight[];
  carePlanItems: CarePlanItem[];
  carePlanScore: number;
  userRole: UserRole;
  onHighlightClick: (highlightId: string, sourceEntryId: string | null) => void;
  onAcceptHighlight: (highlightId: string) => void;
  onRejectHighlight: (highlightId: string) => void;
  loadingAction?: string | null;
  onToggleCarePlanItem?: (index: number) => void;
  conflictCount?: number;
  /** Current care_notes.version, for detecting stale highlight sources. */
  currentNoteVersion?: number | null;
  /**
   * Set when the AI could not answer (503, timeout, unreachable). Drives the
   * amber degraded banner. Distinct from "the AI ran and found nothing" — those
   * look identical on screen unless one of them says so.
   */
  aiDegraded?: boolean;
  onReviewConflicts?: () => void;
  /** Raises the chip to critical styling when an allergy conflict is present. */
  hasCriticalConflict?: boolean;
  /** Assign an open action to a colleague. */
  onAssignAction?: (highlightId: string) => void;
  /** Mark an open action complete. */
  onCompleteAction?: (highlightId: string) => void;
  /** Defer an action. Critical items require a typed reason. */
  onDeferAction?: (highlightId: string) => void;
}

export function TopCard({
  aiDegraded = false,
  currentNoteVersion,
  glanceCache,
  highlights,
  carePlanItems,
  carePlanScore,
  userRole,
  onHighlightClick,
  onAcceptHighlight,
  onRejectHighlight,
  loadingAction,
  onToggleCarePlanItem,
  conflictCount = 0,
  onReviewConflicts,
  hasCriticalConflict = false,
  onAssignAction,
  onCompleteAction,
  onDeferAction,
}: TopCardProps) {
  const topHighlights = [...highlights]
    .filter((h) => h.is_accepted !== false)
    .sort((a, b) => b.importance_score - a.importance_score)
    .slice(0, 3);

  const roleLabel = userRole === 'staff' ? 'Vitals & Compliance' : 'Risks & Decisions';

  return (
    <div className="space-y-4">
      {/* Degraded state, stated in words rather than implied by a colour.
          A tired clinician reading an empty flags list concludes "there are
          none" — the one reading a banner concludes "this was not checked".
          Those are opposite clinical actions, so the difference is spelled
          out rather than left to a subtle visual cue.

          This MUST render independently of conflictCount. It previously sat
          inside the {conflictCount > 0 && ...} block below, which made it dead
          in exactly the scenario it exists for: when the AI is unreachable the
          contradiction check never runs, so conflictCount is 0, so the banner
          never appeared and a clean-looking Glance View read as "nothing
          found" rather than "not checked". */}
      {aiDegraded && (
        <div
          role="alert"
          className="rounded-md border-2 border-amber-600 bg-amber-100 dark:bg-amber-950/50 px-3 py-2"
        >
          <p className="text-sm font-bold uppercase tracking-wide text-amber-900 dark:text-amber-300">
            Offline Mode (Rule-Derived)
          </p>
          <p className="mt-1 text-sm font-medium leading-relaxed text-amber-900 dark:text-amber-200">
            Absence of a flag does not imply absence of clinical concern.
          </p>
        </div>
      )}

      {/* Conflict Warning Banner */}
      {conflictCount > 0 && (
        <Card className="border-amber-200 bg-amber-50">
          <CardContent className="py-3">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-lg bg-amber-100 flex items-center justify-center shrink-0">
                <AlertTriangle className="w-4 h-4 text-amber-600" />
              </div>
              <div className="flex-1">
                <p className="text-sm font-medium text-amber-800">
                  {conflictCount} AI {conflictCount === 1 ? 'summary' : 'summaries'} may conflict with recent notes
                </p>
                <p className="text-xs text-amber-600 mt-0.5">
                  Review to ensure accuracy
                </p>
              </div>
              {onReviewConflicts && (
                <Button
                  size="sm"
                  variant="outline"
                  className="border-amber-300 text-amber-700 hover:bg-amber-100"
                  onClick={onReviewConflicts}
                >
                  Review
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Glance header card */}
      <Card>
        <CardHeader className="pb-2 px-3 sm:px-6">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 sm:w-8 sm:h-8 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                <Eye className="w-4 h-4 text-primary" />
              </div>
              <CardTitle className="text-sm sm:text-base">At a Glance</CardTitle>
            </div>
            <Badge variant="outline" className="text-[10px] sm:text-xs shrink-0 hidden sm:inline-flex">
              {roleLabel}
            </Badge>
          </div>
          {glanceCache.last_visit && (
            <p className="text-xs text-muted-foreground mt-1">
              Last visit: {glanceCache.last_visit}
            </p>
          )}
        </CardHeader>

        <CardContent className="space-y-3 px-3 sm:px-6">
      {/* Critical flags and open actions, above the highlight list: the brief
          asks for both to be readable at a glance, and an unresolved action
          outranks an observation. */}
      <CriticalFlags
        highlights={highlights}
        onFlagClick={onHighlightClick}
        currentNoteVersion={currentNoteVersion}
      />

      {onAssignAction && onCompleteAction && onDeferAction && (
        <div className="mb-3">
          <ActionItems
            highlights={highlights}
            onAssign={onAssignAction}
            onDone={onCompleteAction}
            onDefer={onDeferAction}
          />
        </div>
      )}

      {/* Unresolved contradictions rank above individual highlights: a
          disagreement about a dose is more urgent than any single observation. */}
      {conflictCount > 0 && (
        <div className="mb-3">
          <ConflictSummaryBadge
            count={conflictCount}
            hasCritical={hasCriticalConflict}
            onClick={onReviewConflicts}
          />
        </div>
      )}

          {topHighlights.map((highlight) => (
            <HighlightItem
              key={highlight.id}
              highlight={highlight}
              onClickNavigate={() => onHighlightClick(highlight.id, highlight.source_entry_id)}
              onAccept={() => onAcceptHighlight(highlight.id)}
              onReject={() => onRejectHighlight(highlight.id)}
              showActions={userRole === 'clinician'}
              loadingAction={loadingAction}
            />
          ))}
          {topHighlights.length === 0 && (
            <div className="py-4 text-center">
              <p className="text-sm text-muted-foreground">No highlights to show</p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Care Plan */}
      {carePlanItems.length > 0 && (
        <Card>
          <CardHeader className="pb-2 px-3 sm:px-6">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm">Care Plan</CardTitle>
              <span className={`text-lg sm:text-xl font-bold heading-display ${
                carePlanScore >= 50 ? 'text-primary' : 'text-muted-foreground'
              }`}>
                {Math.round(carePlanScore)}%
              </span>
            </div>
          </CardHeader>
          <CardContent className="space-y-3 px-3 sm:px-6">
            <div className="w-full bg-secondary rounded-full h-2 overflow-hidden">
              <div
                className={`h-2 rounded-full transition-all duration-700 ${
                  'bg-primary'
                }`}
                style={{ width: `${Math.min(carePlanScore, 100)}%` }}
              />
            </div>
            <div className="space-y-1.5">
              {carePlanItems.map((item, idx) => (
                <div
                  key={idx}
                  className={`flex items-start gap-2.5 text-sm ${onToggleCarePlanItem ? 'cursor-pointer hover:bg-secondary/50 rounded px-1 -mx-1 transition-colors' : ''}`}
                  onClick={() => onToggleCarePlanItem?.(idx)}
                  role={onToggleCarePlanItem ? 'button' : undefined}
                  tabIndex={onToggleCarePlanItem ? 0 : undefined}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      onToggleCarePlanItem?.(idx);
                    }
                  }}
                >
                  {item.completed ? (
                    <CheckCircle2 className="w-4 h-4 text-primary shrink-0 mt-0.5" />
                  ) : (
                    <XCircle className="w-4 h-4 text-muted-foreground/50 shrink-0 mt-0.5" />
                  )}
                  <span className={`break-words ${item.completed ? 'text-muted-foreground' : 'font-medium'}`}>
                    {item.label}
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function HighlightItem({
  highlight,
  onClickNavigate,
  onAccept,
  onReject,
  showActions,
  loadingAction,
}: {
  highlight: Highlight;
  onClickNavigate: () => void;
  onAccept: () => void;
  onReject: () => void;
  showActions: boolean;
  loadingAction?: string | null;
}) {
  const riskStyle = getRiskColor(highlight.risk_level);
  const isAI = highlight.created_by === 'system';
  const isInfoLevel = highlight.risk_level === 'info' || highlight.risk_level === 'low';
  const isAcceptedInfoLevel = highlight.is_accepted === true && isInfoLevel;

  // Confidence is deliberately NOT passed here. TrustBadge's `confidence` field
  // used to receive importance_score, which renders queue position as if it
  // were reliability — the exact failure that makes a trust signal meaningless.
  // Measured confidence is shown separately by <ConfidenceBadge>.
  const badgeType = isAI
    ? { type: 'ai_generated' as const, label: 'AI' }
    : { type: 'clinician_verified' as const, label: 'Manual' };

  // True when the deterministic rules, not the model, set this level.
  const floorApplied =
    !!highlight.risk_floor &&
    !!highlight.model_risk &&
    RISK_ORDER[highlight.risk_floor] > RISK_ORDER[highlight.model_risk];

  // Collapsed view for accepted INFO/LOW highlights
  if (isAcceptedInfoLevel) {
    return (
      <div
        className="flex items-center gap-2 py-1.5 px-2 rounded-md bg-secondary/30 cursor-pointer hover:bg-secondary/50 transition-all duration-200 group"
        onClick={onClickNavigate}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter') onClickNavigate();
          if (e.key === 'n' && showActions) { e.preventDefault(); onReject(); }
        }}
      >
        <CheckCircle2 className="w-3.5 h-3.5 text-primary shrink-0" />
        <span className="text-xs text-muted-foreground line-clamp-1 flex-1">
          {highlight.content_snippet}
        </span>
        <Badge variant={highlight.risk_level as 'info' | 'low'} className="text-[10px] px-1.5">
          {highlight.risk_level.toUpperCase()}
        </Badge>
        {showActions && (
          <button
            className="w-5 h-5 rounded border border-border bg-background hover:bg-secondary items-center justify-center transition-colors opacity-0 group-hover:opacity-100 hidden group-hover:flex"
            onClick={(e) => { e.stopPropagation(); onReject(); }}
            title="Undo acceptance (N)"
          >
            <X className="w-3 h-3 text-muted-foreground" />
          </button>
        )}
      </div>
    );
  }

  // Rejected highlights with fade-out animation
  if (highlight.is_accepted === false) {
    return (
      <div
        className="flex items-center gap-2 py-1.5 px-2 rounded-md bg-secondary/40 border border-border opacity-60 cursor-pointer hover:opacity-80 transition-all duration-300 group"
        onClick={onClickNavigate}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'y' && showActions) { e.preventDefault(); onAccept(); }
        }}
      >
        <XCircle className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
        <span className="text-xs text-muted-foreground line-through line-clamp-1 flex-1">
          {highlight.content_snippet}
        </span>
        {showActions && (
          <button
            className="w-5 h-5 rounded bg-green-50 hover:bg-green-100 items-center justify-center transition-colors opacity-0 group-hover:opacity-100 hidden group-hover:flex"
            onClick={(e) => { e.stopPropagation(); onAccept(); }}
            title="Undo rejection (Y)"
          >
            <Check className="w-3 h-3 text-green-500" />
          </button>
        )}
      </div>
    );
  }

  // Full view for pending or accepted CRITICAL/HIGH/MEDIUM highlights
  return (
    <div
      className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer hover:shadow-sm transition-all duration-150 ${riskStyle}`}
      onClick={onClickNavigate}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'y' && showActions) { e.preventDefault(); onAccept(); }
        if (e.key === 'n' && showActions) { e.preventDefault(); onReject(); }
        if (e.key === 'Enter') onClickNavigate();
      }}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 mb-1 flex-wrap">
          <TrustBadge badge={badgeType} size="sm" />
          <Badge variant={highlight.risk_level as 'critical' | 'high' | 'medium' | 'low' | 'info'} className="text-xs">
            {highlight.risk_level.toUpperCase()}
          </Badge>
          {highlight.is_pinned && (
            <span className="text-xs text-muted-foreground">Pinned</span>
          )}
          {highlight.is_accepted === true && (
            <span className="text-xs text-primary font-medium flex items-center gap-0.5">
              <CheckCircle2 className="w-3 h-3" />
              Accepted
            </span>
          )}
        </div>
        <p className="text-sm font-medium leading-snug break-words">{highlight.content_snippet}</p>
        <p className="text-xs text-muted-foreground mt-1 break-words">{highlight.risk_reason}</p>

        {/* Progressive trust disclosure: severity, reliability and the reason
            the level was set are three separate signals, shown as three. */}
        <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
          <ConfidenceBadge
            score={highlight.confidence_score}
            band={highlight.confidence_band}
            abstained={highlight.abstained}
            metadata={highlight.safety_metadata}
            unverified={highlight.safety_metadata?.unverified}
          />
          {floorApplied && (
            <span
              className="inline-flex items-center gap-1 rounded-md border border-blue-200/80 bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-700"
              title={
                `Raised from '${highlight.model_risk}' to '${highlight.risk_floor}' by a ` +
                `deterministic rule: ` +
                (highlight.safety_metadata?.triggered_rules ?? [])
                  .map((r) => r.rationale)
                  .join('; ') +
                `. The model can raise risk but never lower it.`
              }
            >
              Rule floor
            </span>
          )}
        </div>
      </div>

      {showActions && (
        <div className="flex flex-col gap-1 shrink-0">
          {/* Show accept button if not yet accepted */}
          {highlight.is_accepted !== true && (
            <button
              className="w-7 h-7 rounded-md bg-green-50 hover:bg-green-100 flex items-center justify-center transition-colors disabled:opacity-50"
              onClick={(e) => { e.stopPropagation(); onAccept(); }}
              title="Accept (Y)"
              disabled={loadingAction === `accept-${highlight.id}` || loadingAction === `reject-${highlight.id}`}
            >
              {loadingAction === `accept-${highlight.id}` ? (
                <Loader2 className="w-3.5 h-3.5 text-green-600 animate-spin" />
              ) : (
                <Check className="w-3.5 h-3.5 text-green-600" />
              )}
            </button>
          )}
          {/* Always show reject button (to allow changing decision) */}
          <button
            className="w-7 h-7 rounded-md border border-border bg-background hover:bg-secondary flex items-center justify-center transition-colors disabled:opacity-50"
            onClick={(e) => { e.stopPropagation(); onReject(); }}
            title={highlight.is_accepted === true ? "Undo acceptance (N)" : "Reject (N)"}
            disabled={loadingAction === `accept-${highlight.id}` || loadingAction === `reject-${highlight.id}`}
          >
            {loadingAction === `reject-${highlight.id}` ? (
              <Loader2 className="w-3.5 h-3.5 text-muted-foreground animate-spin" />
            ) : (
              <X className="w-3.5 h-3.5 text-muted-foreground" />
            )}
          </button>
        </div>
      )}
    </div>
  );
}
