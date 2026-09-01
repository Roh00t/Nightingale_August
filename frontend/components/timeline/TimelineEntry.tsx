'use client';

import React from 'react';
import { Badge } from '@/components/ui/badge';
import { TrustBadge } from '@/components/ui/trust-badge';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Button } from '@/components/ui/button';
import { InlineComment } from '@/components/editor/InlineComment';
import { getRoleColor, getTrustBadge, getRelativeTime, getFreshnessAge } from '@/lib/utils';
import type { TimelineEntry as TimelineEntryType, Comment, UserRole, Profile } from '@/lib/types';
import { MessageSquare, ExternalLink, Bot, AlertTriangle, FlaskConical , Undo2 } from 'lucide-react';

interface TimelineEntryProps {
  entry: TimelineEntryType;
  comments: Comment[];
  isHighlighted: boolean;
  /**
   * Character span within content_text to flash, from a highlight's
   * provenance_pointer. Ringing the whole entry only tells a clinician which
   * note; the span tells them which words, which is what "traceable to its
   * origin" actually requires.
   */
  highlightedSpan?: { from: number; to: number } | null;
  userRole: UserRole;
  onAddComment: (entryId: string) => void;
  onNavigateToSource: (entryId: string) => void;
  showCommentInput?: boolean;
  currentUser?: Profile | null;
  onSubmitComment?: (entryId: string, content: string, parentId?: string, mentions?: string[]) => void;
  onResolveComment?: (commentId: string) => void;
  clinicMembers?: Profile[];
  /**
   * Withdraw a message already sent to the patient. Supplied only for
   * clinician/admin on an outgoing, not-yet-retracted entry.
   */
  onRetract?: (entryId: string) => void;
}

export function TimelineEntry({
  entry,
  comments,
  isHighlighted,
  highlightedSpan = null,
  userRole,
  onAddComment,
  onNavigateToSource,
  showCommentInput,
  currentUser,
  onSubmitComment,
  onResolveComment,
  clinicMembers = [],
  onRetract,
}: TimelineEntryProps) {
  const trustBadge = getTrustBadge(entry.author_role, entry.entry_type);
  const freshnessAge = getFreshnessAge(entry.created_at);
  const isAI: boolean = entry.entry_type.startsWith('ai_');
  const isLabResult: boolean = entry.entry_type === 'system_event' && entry.content != null && typeof entry.content === 'object' && 'test_name' in (entry.content as object);
  const roleColor = getRoleColor(entry.author_role as UserRole);
  const metadataDirection =
    entry.entry_type === 'patient_message' && typeof entry.metadata?.direction === 'string'
      ? entry.metadata.direction
      : null;
  const entryTypeLabel = isLabResult
    ? 'Lab Result'
    : entry.entry_type === 'patient_message'
    ? (metadataDirection === 'outgoing' ? 'Message to Patient' : 'Patient Update')
    : entry.entry_type.replace(/_/g, ' ');
  const authorInitials = entry.author?.display_name
    ?.split(' ')
    .map((n) => n[0])
    .join('')
    .toUpperCase()
    .slice(0, 2) || (isAI ? 'AI' : '??');

  const entryRef = React.useRef<HTMLDivElement>(null);
  const hasConflict: boolean = entry.metadata?.conflict_flagged === true;

  React.useEffect(() => {
    if (isHighlighted && entryRef.current) {
      entryRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
      entryRef.current.classList.add('pulse-target');
      const timeout = setTimeout(() => {
        entryRef.current?.classList.remove('pulse-target');
      }, 3000);
      return () => clearTimeout(timeout);
    }
  }, [isHighlighted]);

  return (
    <div
      ref={entryRef}
      id={`entry-${entry.id}`}
      className={`freshness-decay relative ${isHighlighted ? 'ring-2 ring-primary ring-offset-2 rounded-lg' : ''}`}
      data-age={freshnessAge}
    >
      <div
        className={`flex gap-3 p-4 rounded-lg border transition-all duration-150 ${
          isAI ? 'bg-secondary/40 border-border' : 'bg-card border-border'
        } ${isHighlighted ? 'shadow-md' : 'hover:shadow-sm hover:border-border'}`}
      >
        {/* Avatar */}
        <div className="flex flex-col items-center">
          <Avatar className="h-8 w-8 shrink-0">
            {isLabResult ? (
              <AvatarFallback className="bg-purple-100 text-purple-600">
                <FlaskConical className="w-4 h-4" />
              </AvatarFallback>
            ) : isAI ? (
              <AvatarFallback className="bg-secondary text-muted-foreground">
                <Bot className="w-4 h-4" />
              </AvatarFallback>
            ) : (
              <AvatarFallback
                style={{ backgroundColor: roleColor, color: 'white' }}
                className="text-xs font-bold"
              >
                {authorInitials}
              </AvatarFallback>
            )}
          </Avatar>
          <div className="w-px flex-1 bg-border/50 mt-2" />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          {/* Header */}
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className="text-sm font-medium">
              {entry.author?.display_name || (isAI ? 'AI Assistant' : 'Unknown')}
            </span>
            <TrustBadge badge={trustBadge} size="sm" />
            {entry.risk_level !== 'info' && (
              <Badge
                variant={entry.risk_level as 'critical' | 'high' | 'medium' | 'low'}
                className="text-xs"
              >
                {entry.risk_level.toUpperCase()}
              </Badge>
            )}
            {entry.visibility === 'patient_visible' && (
              <Badge variant="outline" className="text-xs">
                Patient Visible
              </Badge>
            )}
            <span className="text-xs text-muted-foreground ml-auto">
              {getRelativeTime(entry.created_at)}
            </span>
          </div>

          {/* Entry type */}
          <p className="text-xs text-muted-foreground uppercase tracking-wider mb-2 font-medium">
            {entryTypeLabel}
          </p>

          {/* Conflict Banner */}
          {hasConflict ? (
            <div className="flex items-center gap-2 p-2 mb-2 rounded-md bg-amber-50 border border-amber-200 text-amber-800">
              <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0" />
              <span className="text-xs font-medium">
                This AI summary may conflict with recent clinician notes
              </span>
              <Button
                size="sm"
                variant="ghost"
                className="ml-auto h-6 text-xs text-amber-700 hover:text-amber-900 hover:bg-amber-100"
                onClick={() => onNavigateToSource(entry.id)}
              >
                Review
              </Button>
            </div>
          ) : null}

          {/* Withdrawn state, above the content rather than replacing it: the
              patient and any auditor need to see WHAT was withdrawn, not just
              that something was. */}
          {entry.is_retracted && (
            <div className="mb-2 rounded-md border-2 border-red-600 bg-red-50 dark:bg-red-950/40 px-3 py-2">
              <span className="inline-block rounded bg-red-600 px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide text-white">
                [WITHDRAWN BY CARE TEAM]
              </span>
              {entry.retraction_reason && (
                <p className="mt-1.5 text-sm font-medium text-red-700 dark:text-red-400">
                  Reason: {entry.retraction_reason}
                </p>
              )}
            </div>
          )}

          {/* Content */}
          <div className="text-sm leading-relaxed">
            {isLabResult ? (
              <LabResultsDisplay content={entry.content as unknown as LabResultContent} metadata={entry.metadata} />
            ) : entry.content_text ? (
              <p className={entry.is_retracted ? 'line-through decoration-2 decoration-red-600 opacity-70' : undefined}>
                <SpanHighlightedText text={entry.content_text} span={isHighlighted ? highlightedSpan : null} />
              </p>
            ) : (
              <p className="italic text-muted-foreground">No text content</p>
            )}
          </div>

          {/* Provenance */}
          {entry.provenance_pointer && (
            <button
              className="inline-flex items-center gap-1.5 text-xs text-primary hover:text-primary/80 mt-2 transition-colors"
              onClick={() => onNavigateToSource(entry.provenance_pointer!.source_id)}
            >
              <ExternalLink className="w-3 h-3" />
              View source
            </button>
          )}

          {/* AI Session provenance */}
          {isAI && entry.metadata?.session_id ? (
            <div className="mt-2 text-xs text-muted-foreground flex items-center gap-2">
              <span className="px-1.5 py-0.5 rounded bg-secondary">
                Session: {String(entry.metadata.session_id)}
              </span>
              {entry.metadata?.recording_duration_sec ? (
                <span>
                  ({Math.floor(Number(entry.metadata.recording_duration_sec) / 60)}m {Number(entry.metadata.recording_duration_sec) % 60}s)
                </span>
              ) : null}
            </div>
          ) : null}

          {/* Actions */}
          <div className="flex items-center gap-2 mt-3">
            {userRole !== 'patient' && (
              <Button
                size="sm"
                variant="ghost"
                className="h-7 text-xs gap-1.5 text-muted-foreground hover:text-foreground"
                onClick={() => onAddComment(entry.id)}
              >
                <MessageSquare className="w-3 h-3" />
                {comments.length > 0 ? comments.length : 'Comment'}
              </Button>
            )}

            {/* Withdraw. Offered only on a message that actually reached the
                patient and has not already been withdrawn — the parent decides
                the role, so this stays a presentational check. */}
            {onRetract && !entry.is_retracted && (
              <Button
                size="sm"
                variant="ghost"
                className="h-7 text-xs gap-1.5 text-amber-700 hover:text-amber-800"
                onClick={() => onRetract(entry.id)}
              >
                <Undo2 className="w-3 h-3" />
                Withdraw
              </Button>
            )}
          </div>

          {/* Inline comments display */}
          {comments.length > 0 && !showCommentInput && (
            <div className="mt-3 space-y-2 pl-3 border-l-2 border-border">
              {comments.slice(0, 3).map((comment) => {
                const isMentioned = currentUser && comment.mentions?.includes(currentUser.id);
                return (
                  <div
                    key={comment.id}
                    className={`text-xs p-1.5 -m-1.5 rounded-md transition-colors ${
                      isMentioned ? 'bg-primary/10 ring-1 ring-primary/20' : ''
                    }`}
                  >
                    <span className="font-medium" style={{ color: getRoleColor(comment.author_role as UserRole) }}>
                      {comment.author?.display_name || comment.author_role}:
                    </span>{' '}
                    <span className="text-muted-foreground">{comment.content}</span>
                    {comment.is_resolved && (
                      <Badge variant="outline" className="ml-1 text-xs px-1">
                        Resolved
                      </Badge>
                    )}
                  </div>
                );
              })}
              {comments.length > 3 && (
                <button className="text-xs text-primary hover:text-primary/80 transition-colors">
                  View {comments.length - 3} more...
                </button>
              )}
            </div>
          )}

          {/* InlineComment component when active */}
          {showCommentInput && currentUser && onSubmitComment && onResolveComment && (
            <div className="mt-3 p-3 bg-secondary/30 rounded-lg border border-border">
              <InlineComment
                comments={comments}
                currentUser={currentUser}
                entryId={entry.id}
                onSubmit={(content, parentId, mentions) => onSubmitComment(entry.id, content, parentId, mentions)}
                onResolve={onResolveComment}
                clinicMembers={clinicMembers}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Lab result types and component
interface LabResultItem {
  name: string;
  value: number;
  unit: string;
  reference: string;
  abnormal: boolean;
}

interface LabResultContent {
  test_name: string;
  results: LabResultItem[];
}

function LabResultsDisplay({ content, metadata }: { content: LabResultContent; metadata: Record<string, unknown> }) {
  const abnormalResults = content.results.filter(r => r.abnormal);
  const normalResults = content.results.filter(r => !r.abnormal);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Badge variant="outline" className="bg-purple-50 text-purple-700 border-purple-200">
          <FlaskConical className="w-3 h-3 mr-1" />
          {content.test_name}
        </Badge>
        {metadata?.lab_name ? (
          <span className="text-xs text-muted-foreground">{String(metadata.lab_name)}</span>
        ) : null}
      </div>

      {/* Abnormal results highlighted */}
      {abnormalResults.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-red-600 uppercase tracking-wide">Abnormal</p>
          <div className="grid gap-1.5">
            {abnormalResults.map((result) => (
              <div
                key={result.name}
                className="flex items-center justify-between p-2 rounded-md bg-red-50 border border-red-100"
              >
                <span className="text-sm font-medium text-red-800">{result.name}</span>
                <div className="text-right">
                  <span className="text-sm font-bold text-red-700">
                    {result.value} {result.unit}
                  </span>
                  <span className="text-xs text-red-500 ml-2">
                    (ref: {result.reference})
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Normal results - collapsed by default */}
      {normalResults.length > 0 && (
        <details className="group">
          <summary className="text-xs font-medium text-muted-foreground cursor-pointer hover:text-foreground">
            {normalResults.length} normal result{normalResults.length !== 1 ? 's' : ''}
          </summary>
          <div className="mt-2 grid gap-1">
            {normalResults.map((result) => (
              <div
                key={result.name}
                className="flex items-center justify-between py-1 px-2 text-xs text-muted-foreground"
              >
                <span>{result.name}</span>
                <span>
                  {result.value} {result.unit}
                </span>
              </div>
            ))}
          </div>
        </details>
      )}

      {metadata?.order_id ? (
        <p className="text-xs text-muted-foreground">
          Order: {String(metadata.order_id)}
        </p>
      ) : null}
    </div>
  );
}


/**
 * Renders text with one character span visually flashed.
 *
 * Offsets come from a highlight's provenance_pointer and address the STORED
 * text, which is why the extraction layer records the source form of a quote
 * rather than the model's wording — otherwise these offsets would drift.
 *
 * A span that does not fit the text is ignored rather than clamped: a wrong
 * span points a clinician at words that do not support the claim, which is
 * worse than showing no emphasis at all.
 */
function SpanHighlightedText({
  text,
  span,
}: {
  text: string;
  span?: { from: number; to: number } | null;
}) {
  const valid =
    span &&
    Number.isFinite(span.from) &&
    Number.isFinite(span.to) &&
    span.from >= 0 &&
    span.to > span.from &&
    span.to <= text.length;

  if (!valid) return <>{text}</>;

  return (
    <>
      {text.slice(0, span!.from)}
      <mark className="provenance-flash rounded px-0.5 bg-primary/25 text-foreground">
        {text.slice(span!.from, span!.to)}
      </mark>
      {text.slice(span!.to)}
    </>
  );
}
