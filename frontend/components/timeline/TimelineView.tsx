'use client';

import React, { useMemo, useState, useCallback } from 'react';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Button } from '@/components/ui/button';
import { TimelineEntry } from './TimelineEntry';
import { EntryFilters } from './EntryFilters';
import { useAppStore } from '@/lib/stores/app-store';
import type { TimelineEntry as TimelineEntryType, Comment, UserRole, Profile } from '@/lib/types';
import { SlidersHorizontal, Clock } from 'lucide-react';

interface TimelineViewProps {
  entries: TimelineEntryType[];
  comments: Comment[];
  userRole: UserRole;
  onAddComment: (entryId: string) => void;
  onNavigateToSource: (entryId: string) => void;
  commentingEntryId: string | null;
  currentUser: Profile | null;
  onSubmitComment: (entryId: string, content: string, parentId?: string, mentions?: string[]) => void;
  onResolveComment: (commentId: string) => void;
  clinicMembers?: Profile[];
}

export function TimelineView({
  entries,
  comments,
  userRole,
  onAddComment,
  onNavigateToSource,
  commentingEntryId,
  currentUser,
  onSubmitComment,
  onResolveComment,
  clinicMembers = [],
}: TimelineViewProps) {
  const { highlightedEntryId, timelineFilters, setTimelineFilters } = useAppStore();
  const [showFilters, setShowFilters] = useState(false);

  const toggleRole = useCallback((role: string) => {
    setTimelineFilters({
      authorRoles: timelineFilters.authorRoles.includes(role as UserRole)
        ? timelineFilters.authorRoles.filter((r) => r !== role)
        : [...timelineFilters.authorRoles, role as UserRole],
    });
  }, [timelineFilters.authorRoles, setTimelineFilters]);

  const toggleType = useCallback((type: string) => {
    setTimelineFilters({
      entryTypes: timelineFilters.entryTypes.includes(type)
        ? timelineFilters.entryTypes.filter((t) => t !== type)
        : [...timelineFilters.entryTypes, type],
    });
  }, [timelineFilters.entryTypes, setTimelineFilters]);

  const toggleRisk = useCallback((risk: string) => {
    setTimelineFilters({
      riskLevels: timelineFilters.riskLevels.includes(risk)
        ? timelineFilters.riskLevels.filter((r) => r !== risk)
        : [...timelineFilters.riskLevels, risk],
    });
  }, [timelineFilters.riskLevels, setTimelineFilters]);

  const clearFilters = useCallback(() => {
    setTimelineFilters({
      authorRoles: [],
      entryTypes: [],
      riskLevels: [],
    });
  }, [setTimelineFilters]);

  const filteredEntries = useMemo(() => {
    let result = entries;

    if (timelineFilters.authorRoles.length > 0) {
      result = result.filter((e) => timelineFilters.authorRoles.includes(e.author_role as UserRole));
    }
    if (timelineFilters.entryTypes.length > 0) {
      result = result.filter((e) => timelineFilters.entryTypes.includes(e.entry_type));
    }
    if (timelineFilters.riskLevels.length > 0) {
      result = result.filter((e) => timelineFilters.riskLevels.includes(e.risk_level));
    }

    return result.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
  }, [entries, timelineFilters]);

  const commentsByEntry = useMemo(() => {
    const map = new Map<string, Comment[]>();
    comments.forEach((c) => {
      if (c.timeline_entry_id) {
        const existing = map.get(c.timeline_entry_id) || [];
        existing.push(c);
        map.set(c.timeline_entry_id, existing);
      }
    });
    return map;
  }, [comments]);

  const hasActiveFilters =
    timelineFilters.authorRoles.length > 0 ||
    timelineFilters.entryTypes.length > 0 ||
    timelineFilters.riskLevels.length > 0;

  return (
    <div className="flex flex-col h-full bg-card rounded-lg border border-border overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 sm:px-5 py-3 sm:py-4 border-b border-border gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <div className="w-7 h-7 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
            <Clock className="w-3.5 h-3.5 text-primary" />
          </div>
          <h3 className="text-sm font-semibold heading-display truncate">
            Timeline
          </h3>
          <span className="text-xs text-muted-foreground bg-secondary px-2 py-0.5 rounded-md shrink-0">
            {filteredEntries.length}
          </span>
        </div>
        <Button
          size="sm"
          variant={showFilters ? 'secondary' : 'ghost'}
          className="h-8 text-xs gap-1.5 shrink-0"
          onClick={() => setShowFilters(!showFilters)}
        >
          <SlidersHorizontal className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">Filter</span>
          {hasActiveFilters && (
            <span className="w-1.5 h-1.5 rounded-full bg-primary" />
          )}
        </Button>
      </div>

      {/* Filters */}
      {showFilters && (
        <div className="px-3 sm:px-5 py-3 border-b border-border bg-secondary/30">
          <EntryFilters
            activeRoles={timelineFilters.authorRoles}
            activeTypes={timelineFilters.entryTypes}
            activeRisks={timelineFilters.riskLevels}
            onToggleRole={toggleRole}
            onToggleType={toggleType}
            onToggleRisk={toggleRisk}
            onClearAll={clearFilters}
          />
        </div>
      )}

      {/* Entries */}
      <ScrollArea className="flex-1">
        <div className="p-3 sm:p-4 space-y-3">
          {filteredEntries.length === 0 ? (
            <div className="py-12 text-center">
              <Clock className="w-8 h-8 text-muted-foreground/20 mx-auto mb-2" />
              <p className="text-sm text-muted-foreground">
                No entries match your filters
              </p>
            </div>
          ) : (
            filteredEntries.map((entry) => (
              <TimelineEntry
                key={entry.id}
                entry={entry}
                comments={commentsByEntry.get(entry.id) || []}
                isHighlighted={entry.id === highlightedEntryId}
                userRole={userRole}
                onAddComment={onAddComment}
                onNavigateToSource={onNavigateToSource}
                showCommentInput={commentingEntryId === entry.id}
                currentUser={currentUser}
                onSubmitComment={onSubmitComment}
                onResolveComment={onResolveComment}
                clinicMembers={clinicMembers}
              />
            ))
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
