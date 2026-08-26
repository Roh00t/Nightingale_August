'use client';

import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { TrustBadge } from '@/components/ui/trust-badge';
import { getRiskColor, getConfidenceLabel } from '@/lib/utils';
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
  changesSinceLastVisit: ChangeSinceLastVisit[];
  carePlanItems: CarePlanItem[];
  carePlanScore: number;
  userRole: UserRole;
  onHighlightClick: (highlightId: string, sourceEntryId: string | null) => void;
  onAcceptHighlight: (highlightId: string) => void;
  onRejectHighlight: (highlightId: string) => void;
  loadingAction?: string | null;
  onToggleCarePlanItem?: (index: number) => void;
  conflictCount?: number;
  onReviewConflicts?: () => void;
}

const changeIcons: Record<string, React.ElementType> = {
  new: Plus,
  improved: TrendingUp,
  concerning: TrendingDown,
  unresolved: Clock,
};

const changeColors: Record<string, string> = {
  new: 'text-neutral-600 bg-neutral-50',
  improved: 'text-green-600 bg-green-50',
  concerning: 'text-red-600 bg-red-50',
  unresolved: 'text-neutral-600 bg-neutral-50',
};

export function TopCard({
  glanceCache,
  highlights,
  changesSinceLastVisit,
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
}: TopCardProps) {
  const topHighlights = [...highlights]
    .filter((h) => h.is_accepted !== false)
    .sort((a, b) => b.importance_score - a.importance_score)
    .slice(0, 3);

  const roleLabel = userRole === 'staff' ? 'Vitals & Compliance' : 'Risks & Decisions';

  return (
    <div className="space-y-4">
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

      {/* Changes since last visit */}
      {changesSinceLastVisit.length > 0 && (
        <Card>
          <CardHeader className="pb-2 px-3 sm:px-6">
            <CardTitle className="text-sm">Changes Since Last Visit</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 px-3 sm:px-6">
            {changesSinceLastVisit.map((change, idx) => {
              const Icon = changeIcons[change.type] || AlertCircle;
              const colorClass = changeColors[change.type] || 'text-gray-600 bg-gray-50';
              const [textColor, bgColor] = colorClass.split(' ');

              return (
                <div
                  key={idx}
                  className="flex items-start gap-3 py-1.5"
                >
                  <div className={`w-6 h-6 rounded-lg ${bgColor} flex items-center justify-center shrink-0 mt-0.5`}>
                    <Icon className={`w-3.5 h-3.5 ${textColor}`} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm leading-snug break-words">{change.text}</p>
                    <Badge variant="outline" className="text-[11px] mt-1 px-1.5 break-words">
                      {change.detail}
                    </Badge>
                  </div>
                </div>
              );
            })}
          </CardContent>
        </Card>
      )}

      {/* Care Plan */}
      {carePlanItems.length > 0 && (
        <Card>
          <CardHeader className="pb-2 px-3 sm:px-6">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm">Care Plan</CardTitle>
              <span className={`text-lg sm:text-xl font-bold heading-display ${
                carePlanScore >= 50 ? 'text-primary' : 'text-red-600'
              }`}>
                {Math.round(carePlanScore)}%
              </span>
            </div>
          </CardHeader>
          <CardContent className="space-y-3 px-3 sm:px-6">
            <div className="w-full bg-secondary rounded-full h-2 overflow-hidden">
              <div
                className={`h-2 rounded-full transition-all duration-700 ${
                  carePlanScore >= 50 ? 'bg-primary' : 'bg-red-500'
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
                    <XCircle className="w-4 h-4 text-red-500 shrink-0 mt-0.5" />
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

  const badgeType = isAI
    ? { type: 'ai_generated' as const, label: 'AI', confidence: highlight.importance_score }
    : { type: 'clinician_verified' as const, label: 'Manual' };

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
            className="w-5 h-5 rounded bg-red-50 hover:bg-red-100 items-center justify-center transition-colors opacity-0 group-hover:opacity-100 hidden group-hover:flex"
            onClick={(e) => { e.stopPropagation(); onReject(); }}
            title="Undo acceptance (N)"
          >
            <X className="w-3 h-3 text-red-500" />
          </button>
        )}
      </div>
    );
  }

  // Rejected highlights with fade-out animation
  if (highlight.is_accepted === false) {
    return (
      <div
        className="flex items-center gap-2 py-1.5 px-2 rounded-md bg-red-50/50 border border-red-100 opacity-60 cursor-pointer hover:opacity-80 transition-all duration-300 group"
        onClick={onClickNavigate}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'y' && showActions) { e.preventDefault(); onAccept(); }
        }}
      >
        <XCircle className="w-3.5 h-3.5 text-red-400 shrink-0" />
        <span className="text-xs text-red-600/70 line-through line-clamp-1 flex-1">
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
            className="w-7 h-7 rounded-md bg-red-50 hover:bg-red-100 flex items-center justify-center transition-colors disabled:opacity-50"
            onClick={(e) => { e.stopPropagation(); onReject(); }}
            title={highlight.is_accepted === true ? "Undo acceptance (N)" : "Reject (N)"}
            disabled={loadingAction === `accept-${highlight.id}` || loadingAction === `reject-${highlight.id}`}
          >
            {loadingAction === `reject-${highlight.id}` ? (
              <Loader2 className="w-3.5 h-3.5 text-red-600 animate-spin" />
            ) : (
              <X className="w-3.5 h-3.5 text-red-600" />
            )}
          </button>
        </div>
      )}
    </div>
  );
}
