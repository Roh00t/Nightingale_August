'use client';

import React, { useEffect, useState, useCallback } from 'react';
import dynamic from 'next/dynamic';
import { createClient } from '@/lib/supabase/client';
import { TopCard } from '@/components/glance/TopCard';
import { SunshineBlock } from '@/components/glance/SunshineBlock';
import { VoiceCapture } from '@/components/voice/VoiceCapture';
import { DeferReasonDialog, MIN_REASON_LENGTH } from '@/components/glance/DeferReasonDialog';
import { callAI, AIServiceError, AI_TIMEOUT_MS, type AIFailureKind } from '@/lib/ai_client';
import { deriveOfflineFindings, offlineCoverageNote } from '@/lib/offline_summary';
import { patientSafeGlanceCache } from '@/lib/types';
import { TimelineView } from '@/components/timeline/TimelineView';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { CarePlanCard } from '@/components/patient/CarePlanCard';
import { AIActionsCard } from '@/components/patient/AIActionsCard';
import { DegradedAIPanel } from '@/components/patient/DegradedAIPanel';
import { hasCriticalConflict } from '@/lib/clinical_alerts';
import { useAppStore } from '@/lib/stores/app-store';
import { isApprovedForPatient } from '@/lib/patient_visibility';
import { toast } from 'sonner';
import type {
  CareNote,
  ClinicalConflict,
  TimelineEntry,
  Comment,
  Highlight,
  Profile,
  UserRole,
  AISummarizeResponse,
  ChangeSinceLastVisit,
  CarePlanItem,
} from '@/lib/types';
import { FileText, Heart, Loader2, MessageSquare, Send, X, AlertTriangle } from 'lucide-react';

const CareNoteEditor = dynamic(
  () => import('@/components/editor/CareNoteEditor').then((mod) => ({ default: mod.CareNoteEditor })),
  {
    ssr: false,
    loading: () => <Skeleton className="h-full rounded-lg" />,
  }
);

interface PatientWorkspaceProps {
  /** Patient id from the route. */
  patientId: string;
  /**
   * The care note, already fetched on the server in a single indexed read.
   *
   * This is what makes the Glance View meet its latency budget: the Top Card
   * renders in the server HTML instead of waiting on a client waterfall of
   * session -> care_note -> render. The timeline, comments and highlights are
   * deliberately NOT passed here -- they load client-side below, so historical
   * reads never block the card.
   */
  initialCareNote: CareNote | null;
}

export function PatientWorkspace({ patientId, initialCareNote }: PatientWorkspaceProps) {
  const supabase = createClient();
  const { currentUser, setHighlightedEntryId, setHighlightedSpan } = useAppStore();
  const activeRole = (currentUser?.role || 'clinician') as UserRole;

  const [careNote, setCareNote] = useState<CareNote | null>(initialCareNote);
  const [entries, setEntries] = useState<TimelineEntry[]>([]);
  const [comments, setComments] = useState<Comment[]>([]);
  const [highlights, setHighlights] = useState<Highlight[]>([]);
  const [loading, setLoading] = useState(false);
  const [token, setToken] = useState<string>('');
  const [commentingEntryId, setCommentingEntryId] = useState<string | null>(null);
  const [loadingAction, setLoadingAction] = useState<string | null>(null);
  const [showMessageDraft, setShowMessageDraft] = useState(false);
  const [draftMessage, setDraftMessage] = useState('');
  const [draftKeyPoints, setDraftKeyPoints] = useState<string[]>([]);
  const [generatingDraft, setGeneratingDraft] = useState(false);
  const [sendingMessage, setSendingMessage] = useState(false);
  /**
   * Why the send was refused, when it was. Held rather than toasted: the
   * clinician needs the offending tokens in front of them while they edit, and a
   * toast disappears in four seconds.
   */
  /**
   * Set when the contradiction check could not run. Distinct from "checked and
   * found nothing" — conflating them would show an all-clear during an outage.
   */
  const [conflictsDegraded, setConflictsDegraded] = useState<AIFailureKind | null>(null);

  /**
   * Deterministic findings for the outage path. Memoised because the panel
   * reads it twice (length check, then map) and the rules scan every entry.
   */
  const offlineFindings = React.useMemo(
    () => (conflictsDegraded ? deriveOfflineFindings(entries) : []),
    [conflictsDegraded, entries]
  );
  /** Entry awaiting a withdrawal reason. Null when the dialog is closed. */
  const [retractTarget, setRetractTarget] = useState<string | null>(null);

  const [gateBlock, setGateBlock] = useState<{
    verdict: string;
    message: string;
    ungroundedTerms: string[];
    prohibitedHits: string[];
  } | null>(null);
  const [clinicMembers, setClinicMembers] = useState<Profile[]>([]);

  useEffect(() => {
    let channel: ReturnType<typeof supabase.channel> | null = null;

    async function loadData() {
      const { data: { session } } = await supabase.auth.getSession();
      if (session) setToken(session.access_token);

      // The care note arrived from the server component; only fall back to a
      // client fetch if the server read returned nothing.
      let noteData: CareNote | null = initialCareNote;
      if (!noteData) {
        const { data } = await supabase
          .from('care_notes')
          .select('*')
          .eq('patient_id', patientId)
          .single();
        noteData = (data as CareNote) ?? null;
        if (noteData) setCareNote(noteData);
      }

      if (noteData) {

        // Parallelize data fetching
        const [entryResult, commentResult, highlightResult] = await Promise.all([
          supabase
            .from('timeline_entries')
            .select('*, author:profiles!timeline_entries_author_profile_fkey(*)')
            .eq('care_note_id', noteData.id)
            .order('created_at', { ascending: false }),
          supabase
            .from('comments')
            .select('*, author:profiles!comments_author_profile_fkey(*)')
            .eq('care_note_id', noteData.id)
            .order('created_at', { ascending: true }),
          supabase
            .from('highlights')
            .select('*')
            .eq('care_note_id', noteData.id)
            .order('importance_score', { ascending: false }),
        ]);

        if (entryResult.error) {
          console.warn('Failed to load timeline entries:', entryResult.error.message);
        } else if (entryResult.data) {
          setEntries(entryResult.data as TimelineEntry[]);
        }

        if (commentResult.data) setComments(commentResult.data as Comment[]);
        if (highlightResult.data) setHighlights(highlightResult.data as Highlight[]);

        // Fetch clinic members for @mention functionality
        const { data: membersData } = await supabase
          .from('profiles')
          .select('*')
          .eq('clinic_id', noteData.clinic_id);

        if (membersData) {
          setClinicMembers(membersData as Profile[]);
        }

        // Set up realtime subscriptions after we have the care note ID
        channel = supabase
          .channel('care-note-changes')
          .on('postgres_changes', {
            event: '*',
            schema: 'public',
            table: 'timeline_entries',
            filter: `care_note_id=eq.${noteData.id}`,
          }, async (payload) => {
            if (payload.eventType === 'INSERT') {
              // Fetch the full entry with author data for proper display
              const { data: fullEntry } = await supabase
                .from('timeline_entries')
                .select('*, author:profiles!timeline_entries_author_profile_fkey(*)')
                .eq('id', (payload.new as TimelineEntry).id)
                .single();

              if (fullEntry) {
                setEntries((prev) => {
                  if (prev.some((e) => e.id === fullEntry.id)) return prev;
                  return [fullEntry as TimelineEntry, ...prev];
                });
                toast.info('New timeline entry added');
              }
            }
          })
          .on('postgres_changes', {
            event: '*',
            schema: 'public',
            table: 'highlights',
            filter: `care_note_id=eq.${noteData.id}`,
          }, (payload) => {
            if (payload.eventType === 'INSERT') {
              setHighlights((prev) => [payload.new as Highlight, ...prev]);
            } else if (payload.eventType === 'UPDATE') {
              setHighlights((prev) =>
                prev.map((h) => (h.id === (payload.new as Highlight).id ? payload.new as Highlight : h))
              );
            }
          })
          .on('postgres_changes', {
            event: '*',
            schema: 'public',
            table: 'comments',
            filter: `care_note_id=eq.${noteData.id}`,
          }, async (payload) => {
            if (payload.eventType === 'INSERT') {
              // Fetch the full comment with author data for proper display
              const { data: fullComment } = await supabase
                .from('comments')
                .select('*, author:profiles!comments_author_profile_fkey(*)')
                .eq('id', (payload.new as Comment).id)
                .single();

              if (fullComment) {
                setComments((prev) => {
                  if (prev.some((c) => c.id === fullComment.id)) return prev;
                  return [...prev, fullComment as Comment];
                });
              }
            } else if (payload.eventType === 'UPDATE') {
              setComments((prev) =>
                prev.map((comment) =>
                  comment.id === (payload.new as Comment).id
                    ? { ...comment, ...(payload.new as Comment) }
                    : comment
                )
              );
            }
          })
          .subscribe();
      }

      setLoading(false);
    }

    loadData();

    return () => {
      if (channel) {
        supabase.removeChannel(channel);
      }
    };
  }, [patientId, supabase, initialCareNote]);

  /**
   * Click-to-trace. Scrolls to the source entry AND flashes the exact character
   * span the claim came from, taken from the highlight's provenance_pointer.
   *
   * Navigating to the entry alone answers "which note"; the span answers "which
   * words", which is what makes an AI claim checkable in a couple of seconds
   * rather than requiring the clinician to re-read the whole note.
   */
  /**
   * Clinical contradictions across authors.
   *
   * Detected server-side by POST /api/ai/conflicts. This used to run in a
   * TypeScript port of the Python detector so the UI could flag contradictions
   * without a round trip, but nothing kept the two copies in lockstep — they
   * could drift until one flagged a dosing conflict and the other did not.
   * For a safety control that is not an acceptable failure mode, so there is
   * now one implementation and this calls it.
   *
   * Degrades quietly: if the AI service is unreachable, no conflicts render.
   * Never blocks the timeline, and never invents a "no conflicts" assurance —
   * `conflictsChecked` distinguishes "none found" from "not checked".
   */
  const [conflicts, setConflicts] = useState<ClinicalConflict[]>([]);
  const [conflictsChecked, setConflictsChecked] = useState(false);
  const conflictCount = conflicts.length;

  useEffect(() => {
    // Contradiction detection is a care-team endpoint. Without this the effect
    // fires for a patient and takes a 403 it can do nothing with — a wasted
    // round trip on every patient page load, and a confusing line in the log.
    if (!token || entries.length === 0) return;
    if (currentUser && currentUser.role === 'patient') return;
    let cancelled = false;

    (async () => {
      try {
        const data = await callAI<{ conflicts?: ClinicalConflict[] }>(
          '/api/ai/conflicts',
          {
            entries: entries.map((e) => ({
              id: e.id,
              author_id: e.author_id,
              author_role: e.author_role,
              content_text: e.content_text,
              created_at: e.created_at,
            })),
          },
          token,
        );
        if (!cancelled) {
          setConflicts(data.conflicts ?? []);
          setConflictsChecked(true);
        }
      } catch (err) {
        // The record is unaffected either way — contradictions are derived, not
        // stored. But the two failures mean different things to a clinician and
        // must not be collapsed:
        //
        //   degraded  the check did not run. "No contradictions shown" is NOT
        //             evidence there are none, and the banner has to say so.
        //   otherwise nothing to report.
        //
        // Leaving conflictsChecked false is what keeps the UI from rendering an
        // all-clear it cannot support.
        if (!cancelled && err instanceof AIServiceError && err.shouldDegrade) {
          setConflictsDegraded(err.kind);
        }
      }
    })();

    return () => { cancelled = true; };
  }, [entries, token, currentUser]);

  const handleReviewConflicts = useCallback(() => {
    const first = conflicts[0];
    if (!first?.claims?.length) return;
    const entryId = first.claims[0].entry_id;
    setHighlightedEntryId(entryId);
    setHighlightedSpan(null);
    document.getElementById(`entry-${entryId}`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    toast.warning(
      `${first.conflict_class} conflict: ` +
      first.claims.map((c) => `${c.author_role} noted ${c.value}`).join(' vs ') +
      ' — needs clinician resolution'
    );
    setTimeout(() => setHighlightedEntryId(null), 6000);
  }, [conflicts, setHighlightedEntryId, setHighlightedSpan]);


  /**
   * Open-action handlers.
   *
   * Deferring a critical or high finding requires a typed reason and cannot be
   * done in one click. That friction is deliberate and mirrors
   * `services/safety/feedback.py`: a care team under load will otherwise clear
   * alerts on autopilot, and the loop would then learn to hide exactly the
   * class of finding that matters most. Low-risk items stay one click, because
   * friction on noise is itself a fatigue driver.
   */
  const logInteraction = useCallback(
    async (action: string, highlightId: string, metadata: Record<string, unknown> = {}) => {
      if (!currentUser) return;
      await supabase.from('interaction_log').insert({
        user_id: currentUser.id,
        user_role: currentUser.role,
        action_type: action,
        target_type: 'highlight',
        target_id: highlightId,
        // Metadata only — never the clinical snippet.
        target_metadata: metadata,
      });
    },
    [supabase, currentUser],
  );

  const handleAssignAction = useCallback(async (highlightId: string) => {
    const highlight = highlights.find((h) => h.id === highlightId);
    if (!highlight || !careNote || !currentUser) return;

    const assignable = clinicMembers.filter(
      (m) => m.role === 'staff' || m.role === 'clinician',
    );
    if (assignable.length === 0) {
      toast.error('No colleagues available to assign in this clinic');
      return;
    }

    const assignee = assignable[0];
    const { error } = await supabase.from('comments').insert({
      care_note_id: careNote.id,
      timeline_entry_id: highlight.source_entry_id,
      author_id: currentUser.id,
      author_role: currentUser.role,
      content: `@${assignee.display_name} assigned: ${highlight.content_snippet}`,
      mentions: [assignee.id],
    });

    if (error) {
      toast.error('Could not assign this action');
      return;
    }
    await logInteraction('comment', highlightId, { topic: 'assignment' });
    toast.success(`Assigned to ${assignee.display_name}`);
  }, [highlights, careNote, currentUser, clinicMembers, supabase, logInteraction]);

  const handleCompleteAction = useCallback(async (highlightId: string) => {
    await supabase.from('highlights').update({ is_accepted: true }).eq('id', highlightId);
    setHighlights((prev) =>
      prev.map((h) => (h.id === highlightId ? { ...h, is_accepted: true } : h)),
    );
    await logInteraction('accept', highlightId, { topic: 'action_completed' });
    toast.success('Action marked complete');
  }, [supabase, logInteraction]);

  /**
   * Deferring a critical or high finding opens a mandatory reason dialog.
   *
   * This used window.prompt, which browsers suppress after repeated use and
   * sandboxed iframes block outright — a suppressed prompt returns null, which
   * read as "no reason given" and silently cancelled the defer.
   */
  const [deferTarget, setDeferTarget] = useState<Highlight | null>(null);

  const recordDefer = useCallback(async (highlightId: string, reason: string) => {
    await logInteraction('dismiss', highlightId, {
      topic: 'action_deferred',
      ...(reason ? { reason } : {}),
    });
  }, [logInteraction]);

  const handleDeferAction = useCallback(async (highlightId: string) => {
    const highlight = highlights.find((h) => h.id === highlightId);
    if (!highlight) return;

    const needsReason =
      highlight.risk_level === 'critical' || highlight.risk_level === 'high';

    if (needsReason) {
      setDeferTarget(highlight);   // dialog collects the reason
      return;
    }

    // Low-risk noise stays one click: friction on noise is itself a fatigue driver.
    await recordDefer(highlightId, '');
    toast.info('Action deferred');
  }, [highlights, recordDefer]);

  const confirmDefer = useCallback(async (reason: string) => {
    if (!deferTarget) return;
    // Mirrors the backend policy, so the UI cannot be the weaker of the two.
    if (reason.trim().length < MIN_REASON_LENGTH) return;
    await recordDefer(deferTarget.id, reason.trim());
    setDeferTarget(null);
    toast.info('Deferred with reason recorded');
  }, [deferTarget, recordDefer]);

  /**
   * An ambient capture has already been filed by the time this runs.
   *
   * The entry is written server-side by /api/ai/transcribe using the
   * service-role key. It deliberately does NOT happen here: every INSERT policy
   * on timeline_entries requires `author_id = auth.uid()`, while an AI-scribed
   * entry carries author_role='system' with author_id=NULL. No role can satisfy
   * that — clinician and admin fail exactly as staff does — because a user
   * session that could write author_role='system' could forge a note
   * attributed to the AI scribe.
   *
   * This handler only reflects the result into local state so the new entry
   * appears without a reload.
   */
  const handleVoiceSummary = useCallback(async (result: {
    summary: string;
    entry_type: string;
    timeline_entry_id?: string | null;
    filed?: boolean;
    transcription: Record<string, unknown>;
  }) => {
    if (!careNote || !result.summary?.trim()) return;

    if (!result.filed || !result.timeline_entry_id) {
      // The summary exists but was not persisted — say so rather than letting
      // the clinician assume it is in the record.
      toast.warning('Summary produced but not filed. Re-open the patient and retry.');
      return;
    }

    // Read the row back through RLS: if the caller cannot see it, it should not
    // appear in their timeline either.
    const { data: entry, error } = await supabase
      .from('timeline_entries')
      .select('*, author:profiles!timeline_entries_author_profile_fkey(*)')
      .eq('id', result.timeline_entry_id)
      .single();

    if (error) {
      console.error(
        'Ambient capture filed but could not be read back:',
        error.message || error.details || error.hint || error.code || JSON.stringify(error),
      );
      toast.info('Summary filed. Refresh to see it in the timeline.');
      return;
    }

    setEntries((prev) =>
      prev.some((e) => e.id === entry.id) ? prev : [entry as TimelineEntry, ...prev],
    );
    toast.success('Consult summary added to the timeline');
  }, [careNote, supabase]);

  const handleHighlightClick = useCallback((highlightId: string, sourceEntryId: string | null) => {
    if (!sourceEntryId) return;

    const highlight = highlights.find((h) => h.id === highlightId);
    const pointer = highlight?.provenance_pointer;
    const span =
      pointer && typeof pointer === 'object' && 'span' in pointer
        ? (pointer as { span?: { from: number; to: number } }).span ?? null
        : null;

    setHighlightedEntryId(sourceEntryId);
    // A zero-width span means "span unknown" — the extraction layer records
    // (0,0) rather than fabricating offsets, so treat it as no span.
    setHighlightedSpan(span && span.to > span.from ? span : null);

    document.getElementById(`entry-${sourceEntryId}`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' });

    setTimeout(() => {
      setHighlightedEntryId(null);
      setHighlightedSpan(null);
    }, 6000);
  }, [highlights, setHighlightedEntryId, setHighlightedSpan]);

  const handleAcceptHighlight = useCallback(async (highlightId: string) => {
    setLoadingAction(`accept-${highlightId}`);
    try {
      await supabase
        .from('highlights')
        .update({ is_accepted: true })
        .eq('id', highlightId);

      if (currentUser) {
        await supabase.from('interaction_log').insert({
          user_id: currentUser.id,
          user_role: currentUser.role,
          action_type: 'accept',
          target_type: 'highlight',
          target_id: highlightId,
          target_metadata: {},
        });
      }

      setHighlights((prev) =>
        prev.map((h) => (h.id === highlightId ? { ...h, is_accepted: true } : h))
      );
      toast.success('Highlight accepted');
    } finally {
      setLoadingAction(null);
    }
  }, [supabase, currentUser]);

  const handleRejectHighlight = useCallback(async (highlightId: string) => {
    setLoadingAction(`reject-${highlightId}`);
    try {
      await supabase
        .from('highlights')
        .update({ is_accepted: false })
        .eq('id', highlightId);

      if (currentUser) {
        await supabase.from('interaction_log').insert({
          user_id: currentUser.id,
          user_role: currentUser.role,
          action_type: 'reject',
          target_type: 'highlight',
          target_id: highlightId,
          target_metadata: {},
        });
      }

      setHighlights((prev) =>
        prev.map((h) => (h.id === highlightId ? { ...h, is_accepted: false } : h))
      );
      toast.info('Highlight rejected');
    } finally {
      setLoadingAction(null);
    }
  }, [supabase, currentUser]);

  const handleAddComment = useCallback((entryId: string) => {
    setCommentingEntryId((prev) => (prev === entryId ? null : entryId));
  }, []);

  const handleSubmitComment = useCallback(async (entryId: string, content: string, parentId?: string, mentions?: string[]) => {
    if (!careNote || !currentUser) return;

    const { data: newComment, error } = await supabase.from('comments').insert({
      care_note_id: careNote.id,
      timeline_entry_id: entryId,
      author_id: currentUser.id,
      author_role: currentUser.role,
      content,
      parent_comment_id: parentId || null,
      mentions: mentions || [],
    })
    .select('*, author:profiles!comments_author_profile_fkey(*)')
    .single();

    if (error) {
      toast.error('Failed to save comment');
      console.error('Comment insert error:', error);
    } else if (newComment) {
      setComments((prev) => [...prev, newComment as Comment]);
      toast.success('Comment added');
    }
  }, [supabase, careNote, currentUser]);

  const handleResolveComment = useCallback(async (commentId: string) => {
    if (!currentUser) return;

    const { error } = await supabase
      .from('comments')
      .update({ is_resolved: true, resolved_by: currentUser.id })
      .eq('id', commentId);

    if (error) {
      toast.error('Failed to resolve comment');
    } else {
      setComments((prev) =>
        prev.map((c) => (c.id === commentId ? { ...c, is_resolved: true, resolved_by: currentUser.id } : c))
      );
      toast.success('Comment resolved');
    }
  }, [supabase, currentUser]);

  const handleNavigateToSource = useCallback((entryId: string) => {
    setHighlightedEntryId(entryId);
    const element = document.getElementById(`entry-${entryId}`);
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
    setTimeout(() => setHighlightedEntryId(null), 4000);
  }, [setHighlightedEntryId]);

  const handleCreateTimelineEntry = useCallback(async (
    contentJson: Record<string, unknown>,
    contentText: string
  ): Promise<void> => {
    if (!careNote || !currentUser) return;

    // Get the authenticated user directly from the session to ensure
    // author_id matches auth.uid() for RLS policy compliance
    const { data: { user: authUser } } = await supabase.auth.getUser();
    if (!authUser) {
      toast.error('Session expired. Please refresh the page.');
      return;
    }

    // Step 1: Insert without RETURNING to avoid any SELECT RLS interaction
    const { error: insertError } = await supabase
      .from('timeline_entries')
      .insert({
        care_note_id: careNote.id,
        entry_type: 'manual_note',
        author_role: currentUser.role,
        author_id: authUser.id,
        content: contentJson,
        content_text: contentText,
        risk_level: 'info',
        visibility: 'internal',
        metadata: {},
      });

    if (insertError) {
      console.error('[Timeline] Insert failed:', JSON.stringify(insertError, null, 2));
      console.error('[Timeline] Debug:', {
        care_note_id: careNote.id,
        author_id: authUser.id,
        currentUser_id: currentUser.id,
        ids_match: authUser.id === currentUser.id,
        author_role: currentUser.role,
      });
      throw insertError;
    }

    // Step 2: Fetch the most recent entry we just created
    const { data: newEntry } = await supabase
      .from('timeline_entries')
      .select('*, author:profiles!timeline_entries_author_profile_fkey(*)')
      .eq('care_note_id', careNote.id)
      .eq('author_id', authUser.id)
      .eq('entry_type', 'manual_note')
      .order('created_at', { ascending: false })
      .limit(1)
      .single();

    if (newEntry) {
      setEntries((prev) => [newEntry as TimelineEntry, ...prev]);
    }
  }, [careNote, currentUser, supabase]);

  const handleToggleCarePlanItem = useCallback(async (index: number) => {
    if (!careNote) return;

    const items = [...(careNote.glance_cache.care_plan_items || [])];
    if (index < 0 || index >= items.length) return;

    items[index] = { ...items[index], completed: !items[index].completed };
    const resolvedCount = items.filter((i) => i.completed).length;
    const newScore = items.length > 0 ? Math.round((resolvedCount / items.length) * 100) : 0;

    // Two shapes, deliberately. The clinician on screen should keep seeing the
    // assessment; the database must never receive it.
    //
    // careNote.glance_cache is the object /patients/[id] recomposed for display,
    // so it carries top_items for care-team viewers. Spreading it straight into
    // an update() is what wrote the assessment back into the patient-readable
    // column after it had already been fixed once.
    const displayCache = {
      ...careNote.glance_cache,
      care_plan_items: items,
      care_plan_score: newScore,
    };
    const persistedCache = patientSafeGlanceCache(displayCache);

    // Optimistically update local state — display shape, assessment intact.
    setCareNote((prev) => prev ? { ...prev, glance_cache: displayCache } : prev);

    // Persist the stripped shape. The database strips these keys again on write
    // (20260901000002_glance_cache_guard.sql); this is so the app does not depend on
    // that happening silently.
    const { error } = await supabase
      .from('care_notes')
      .update({ glance_cache: persistedCache })
      .eq('id', careNote.id);

    if (error) {
      console.error('Failed to toggle care plan item:', error);
      toast.error('Failed to update care plan item');
      // Revert optimistic update
      setCareNote((prev) => prev ? { ...prev, glance_cache: careNote.glance_cache } : prev);
    }
  }, [careNote, supabase]);

  const handleDraftPatientMessage = useCallback(async () => {
    if (!careNote || !currentUser) return;
    if (entries.length === 0) {
      toast.error('No timeline entries to draft a message from');
      return;
    }

    setShowMessageDraft(true);
    setGeneratingDraft(true);
    setDraftMessage('');
    setDraftKeyPoints([]);

    try {
      const data = await callAI<{ draft_message?: string; key_points?: string[] }>(
        '/api/ai/draft-patient-message',
        {
          care_note_id: careNote.id,
          entries: entries.map((e) => ({
            entry_id: e.id,
            content: e.content_text || '',
            entry_type: e.entry_type || 'note',
            created_at: e.created_at,
          })),
          author_role: currentUser.role,
        },
        token,
      );
      setDraftMessage(data.draft_message || '');
      setDraftKeyPoints(data.key_points || []);
    } catch (err) {
      // Drafting is the one AI failure with no safe degraded output: a
      // rule-derived "message to the patient" is not a thing that can exist.
      // So the panel closes and the clinician writes it themselves, which is
      // always available.
      const msg =
        err instanceof AIServiceError && err.kind === 'timeout'
          ? 'AI draft timed out. Write the message directly, or try again.'
          : 'AI service unavailable. Write the message directly, or try again.';
      toast.error(msg);
      setShowMessageDraft(false);
    } finally {
      setGeneratingDraft(false);
    }
  }, [careNote, entries, currentUser]);


  /**
   * Withdraw a message already sent to the patient.
   *
   * A gate on sending is only half of maker-checker; the other half is what
   * happens when something wrong gets through anyway. Without this the honest
   * answer was "nothing", which makes the approval step carry weight it cannot
   * support.
   *
   * The reason is required and shown to the patient verbatim, so it is prompted
   * for rather than defaulted — a generic "withdrawn" alarms without informing.
   */
  /**
   * Step 1: record which message is being withdrawn. The reason is collected by
   * DeferReasonDialog, not window.prompt.
   *
   * window.prompt was the wrong primitive here for the same reason it was wrong
   * for deferrals: browsers suppress it after repeated use, and a suppressed
   * prompt returns null — which this handler could not distinguish from the
   * clinician pressing Cancel. The second retraction in a session would have
   * silently done nothing while looking like a deliberate abort. See
   * DeferReasonDialog.tsx, which already exists for exactly this.
   */
  const handleRetractMessage = useCallback((entryId: string) => {
    if (!careNote || !currentUser) return;
    setRetractTarget(entryId);
  }, [careNote, currentUser]);

  /** Step 2: the dialog supplied a validated reason. Perform the retraction. */
  const confirmRetraction = useCallback(async (reason: string) => {
    const entryId = retractTarget;
    if (!careNote || !currentUser || !entryId) return;
    setRetractTarget(null);

    try {
      await callAI<{ retracted_entry_id: string; notice_entry_id: string }>(
        '/api/ai/retract-patient-message',
        { care_note_id: careNote.id, entry_id: entryId, reason: reason.trim() },
        token,
      );

      // Re-read through the user's own session so the timeline shows exactly
      // what RLS will show on reload — both the struck-through original and the
      // new retraction notice — rather than a locally patched guess.
      const { data: refreshed } = await supabase
        .from('timeline_entries')
        .select('*, author:profiles!timeline_entries_author_profile_fkey(*)')
        .eq('care_note_id', careNote.id)
        .order('created_at', { ascending: false });
      if (refreshed) setEntries(refreshed as TimelineEntry[]);

      toast.success('Message withdrawn. The patient has been notified.');
    } catch (err) {
      toast.error(
        err instanceof AIServiceError && err.kind === 'timeout'
          ? 'Timed out. Reload to check whether the withdrawal went through before retrying.'
          : 'Could not withdraw the message.'
      );
    }
  }, [careNote, currentUser, supabase, token, retractTarget]);

  /**
   * Send a patient-facing message — through the maker-checker gate, never around it.
   *
   * The clinician can edit the draft freely before sending, and that is the whole
   * risk: the AI's draft may have been grounded, but "10mg" edited to "100mg" is
   * not, and it is the edited text the patient reads. So what gets screened is
   * `draftMessage` as it stands at the moment of the click.
   *
   * This component no longer inserts into `timeline_entries` itself. The AI
   * service screens and, only on the passing branch, writes the entry with the
   * service-role key. Two reasons that matters more than it looks:
   *
   *   - Grounding is checked against sources the SERVER reads from the record.
   *     If this component sent the sources, a fabricated dose could be shipped as
   *     its own grounding and pass.
   *   - A check performed here before an insert performed here is advice. Any
   *     request made outside this UI — the clinician's own token in curl — would
   *     skip it, because RLS permits a clinician to write timeline entries.
   */
  const handleSendPatientMessage = useCallback(async () => {
    if (!careNote || !currentUser || !draftMessage.trim()) return;

    setSendingMessage(true);
    setGateBlock(null);
    try {
      const sent = await callAI<{ entry_id: string }>(
        '/api/ai/send-patient-message',
        {
          care_note_id: careNote.id,
          // The edited text, not the original draft.
          draft: draftMessage,
        },
        token,
      );

      // Read the row back through the user's own session, so the timeline shows
      // exactly what RLS will show on the next load rather than a local guess.
      const { data: newEntry } = await supabase
        .from('timeline_entries')
        .select('*, author:profiles!timeline_entries_author_profile_fkey(*)')
        .eq('id', sent.entry_id)
        .maybeSingle();

      if (newEntry) {
        setEntries((prev) => [newEntry as TimelineEntry, ...prev]);
      }

      setShowMessageDraft(false);
      setDraftMessage('');
      setDraftKeyPoints([]);
      toast.success('Care instructions sent to patient');
    } catch (err) {
      if (err instanceof AIServiceError && err.kind === 'rejected') {
        // A considered refusal from the gate. No row was written — the service
        // screens before it writes — so this is a state to render, not an error.
        //
        // Note this branch must NOT degrade to a fallback. A timeout can fall
        // back to cached data; a refusal cannot fall back to sending anyway.
        const d = (err.detail ?? {}) as Record<string, unknown>;
        setGateBlock({
          verdict: (d.verdict as string) ?? 'blocked',
          message: (d.message as string) ?? 'This message cannot be sent as written.',
          ungroundedTerms: (d.ungrounded_terms as string[]) ?? [],
          prohibitedHits: (d.prohibited_hits as string[]) ?? [],
        });
        return;
      }
      // Timeout is the dangerous one here: the request may have been received
      // and the entry written after we stopped waiting. So the wording must not
      // promise it was not sent — it says the outcome is unknown and to check,
      // rather than inviting a duplicate message to the patient.
      toast.error(
        err instanceof AIServiceError && err.kind === 'timeout'
          ? 'Timed out waiting for confirmation. Reload the timeline to check whether it sent before resending.'
          : 'AI service unavailable — the message was not sent.'
      );
    } finally {
      setSendingMessage(false);
    }
  }, [careNote, currentUser, draftMessage, supabase, token]);

  const handleSendPatientUpdate = useCallback(async () => {
    if (!careNote || !currentUser || !draftMessage.trim()) return;

    setSendingMessage(true);
    try {
      const { data: { user: authUser } } = await supabase.auth.getUser();
      if (!authUser) {
        toast.error('Session expired. Please refresh the page.');
        return;
      }

      const { error: insertError } = await supabase
        .from('timeline_entries')
        .insert({
          care_note_id: careNote.id,
          entry_type: 'patient_message',
          author_role: 'patient',
          author_id: authUser.id,
          content: {
            type: 'doc',
            content: [{ type: 'paragraph', content: [{ type: 'text', text: draftMessage }] }],
          },
          content_text: draftMessage,
          risk_level: 'info',
          visibility: 'internal',
          metadata: {
            direction: 'incoming',
          },
        });

      if (insertError) {
        console.error('Failed to send patient update:', insertError);
        toast.error('Failed to send update');
        return;
      }

      const { data: newEntry } = await supabase
        .from('timeline_entries')
        .select('*, author:profiles!timeline_entries_author_profile_fkey(*)')
        .eq('care_note_id', careNote.id)
        .eq('author_id', authUser.id)
        .eq('entry_type', 'patient_message')
        .order('created_at', { ascending: false })
        .limit(1)
        .single();

      if (newEntry) {
        setEntries((prev) => [newEntry as TimelineEntry, ...prev]);
      }

      setDraftMessage('');
      toast.success('Update sent to your care team');
    } catch {
      toast.error('Failed to send update');
    } finally {
      setSendingMessage(false);
    }
  }, [careNote, currentUser, draftMessage, supabase]);

  const handleRequestAISummary = useCallback(async () => {
    if (!careNote || !currentUser) return;
    if (entries.length === 0) {
      toast.error('No timeline entries to summarize');
      return;
    }
    setLoadingAction('ai-summary');
    toast.info('Generating AI summary...');

    try {
      const data = await callAI<AISummarizeResponse>(
        '/api/ai/summarize',
        {
          care_note_id: careNote.id,
          entries: entries.map((e) => ({
            entry_id: e.id,
            content: e.content_text || '',
            entry_type: e.entry_type || 'note',
            created_at: e.created_at,
          })),
        },
        token,
      );

      {

        // AI highlights are plain strings — use a default risk level
        const entryRiskLevel = 'info' as const;

        // Check for potential conflicts with recent clinician entries
        const recentClinicianEntries = entries.filter(
          (e) => e.author_role === 'clinician' && !e.entry_type.startsWith('ai_')
        );
        const hasConflict = recentClinicianEntries.length > 0;

        // Insert timeline entry for the AI summary (split insert/select to avoid RLS issues)
        const { error: entryError } = await supabase
          .from('timeline_entries')
          .insert({
            care_note_id: careNote.id,
            entry_type: 'ai_doctor_consult_summary',
            author_role: 'system',
            author_id: currentUser!.id,
            content: {
              type: 'doc',
              content: [{ type: 'paragraph', content: [{ type: 'text', text: data.patient_summary }] }],
            },
            content_text: data.patient_summary,
            risk_level: entryRiskLevel,
            visibility: 'internal',
            metadata: hasConflict ? { conflict_flagged: true } : {},
          });

        if (entryError) {
          console.error('Failed to save AI summary entry:', JSON.stringify(entryError, null, 2));
          toast.error('Failed to save AI summary');
          return;
        }

        // Fetch the entry we just created
        const { data: newEntry } = await supabase
          .from('timeline_entries')
          .select('*, author:profiles!timeline_entries_author_profile_fkey(*)')
          .eq('care_note_id', careNote.id)
          .eq('author_id', currentUser!.id)
          .eq('entry_type', 'ai_doctor_consult_summary')
          .order('created_at', { ascending: false })
          .limit(1)
          .single();

        // Add entry to local state immediately (don't wait for realtime)
        if (newEntry) {
          setEntries((prev) => [newEntry as TimelineEntry, ...prev]);
        }

        // Insert highlights — map plain strings to the required DB schema
        if (data.highlights.length > 0 && newEntry) {
          const highlightRows = data.highlights.map((text, index) => ({
            care_note_id: careNote.id,
            source_entry_id: newEntry.id,
            content_snippet: text,
            risk_reason: 'AI-identified highlight',
            risk_level: 'info' as const,
            importance_score: Math.max(0.1, 1 - index * 0.15),
            provenance_pointer: { source_type: 'ai_summary', source_id: newEntry.id },
            created_by: 'system',
          }));

          const { error: highlightError } = await supabase
            .from('highlights')
            .insert(highlightRows);

          if (highlightError) {
            console.error('Failed to save highlights:', highlightError);
          }
        }

        // Transform AI response to internal rendering types
        const transformedChanges: ChangeSinceLastVisit[] =
          data.changes_since_last_visit.map((text) => ({
            type: 'new' as const,
            symbol: '+',
            text,
            detail: 'AI summary',
          }));

        // Merge AI items with existing items to preserve user's progress
        const existingItems = careNote.glance_cache.care_plan_items || [];

        // Create a map of existing items by normalized label for quick lookup
        const existingItemsMap = new Map(
          existingItems.map(item => [item.label.toLowerCase().trim(), item])
        );

        // Merge AI items with existing, preserving user's completed status
        const mergedCarePlanItems: CarePlanItem[] = data.care_plan_items.map((aiItem) => {
          const normalizedLabel = aiItem.item.toLowerCase().trim();
          const existingItem = existingItemsMap.get(normalizedLabel);

          if (existingItem) {
            // Preserve user's completed status for existing items
            existingItemsMap.delete(normalizedLabel); // Mark as processed
            return {
              label: aiItem.item, // Use AI's label (might have better formatting)
              completed: existingItem.completed, // Keep user's status
            };
          }

          // New item from AI
          return {
            label: aiItem.item,
            completed: aiItem.status === 'resolved',
          };
        });

        // Add any remaining existing items that AI didn't mention (user-added items)
        existingItemsMap.forEach((item) => {
          mergedCarePlanItems.push(item);
        });

        // Compute score from merged care plan items
        const resolvedCount = mergedCarePlanItems.filter((i) => i.completed).length;
        const computedScore = mergedCarePlanItems.length > 0
          ? Math.round((resolvedCount / mergedCarePlanItems.length) * 100)
          : 0;

        // Update glance_cache with care plan data
        const { error: cacheError } = await supabase
          .from('care_notes')
          .update({
            glance_cache: {
              ...patientSafeGlanceCache(careNote.glance_cache),
              care_plan_score: computedScore,
              care_plan_items: mergedCarePlanItems,
              // changes_since_last_visit is deliberately NOT written here. It is
              // part of the clinical assessment and belongs in
              // care_note_assessments; writing it to glance_cache puts it in
              // front of the patient.
            },
          })
          .eq('id', careNote.id);

        if (cacheError) {
          console.error('Failed to update glance cache:', cacheError);
        } else {
          setCareNote((prev) => prev ? {
            ...prev,
            glance_cache: {
              ...prev.glance_cache,
              care_plan_score: computedScore,
              changes_since_last_visit: transformedChanges,
              care_plan_items: mergedCarePlanItems,
            },
          } : prev);
        }

        toast.success('AI summary generated and saved!');
      }
    } catch (err) {
      // Summarisation failing leaves the record untouched — the timeline and
      // glance cache are read from Supabase and are unaffected by the model
      // being unreachable. The message says which of the two happened so the
      // clinician knows whether retrying is worth the wait.
      toast.error(
        err instanceof AIServiceError && err.kind === 'timeout'
          ? `AI summary timed out after ${Math.round(AI_TIMEOUT_MS / 1000)}s. The record below is unchanged.`
          : 'AI service unavailable. The record below is unchanged.'
      );
    } finally {
      setLoadingAction(null);
    }
  }, [careNote, entries, supabase, currentUser, token]);

  if (loading) {
    return (
      <div className="flex flex-col lg:grid lg:grid-cols-12 gap-5 p-4 sm:p-6 h-full overflow-auto">
        <div className="lg:col-span-3 space-y-4">
          <Skeleton className="h-64 rounded-lg" />
          <Skeleton className="h-48 rounded-lg" />
        </div>
        <div className="lg:col-span-5">
          <Skeleton className="h-64 lg:h-full rounded-lg" />
        </div>
        <div className="lg:col-span-4">
          <Skeleton className="h-64 lg:h-full rounded-lg" />
        </div>
      </div>
    );
  }

  if (!careNote) {
    return (
      <div className="flex items-center justify-center h-full">
        <Card>
          <CardContent className="py-16 text-center">
            <FileText className="w-10 h-10 text-muted-foreground/30 mx-auto mb-3" />
            <p className="text-muted-foreground">No care note found for this patient.</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  // Patient view
  if (activeRole === 'patient') {
    // Messages TO the patient from care team (outgoing patient_message or instruction)
    const careInstructions = entries.filter(
      (e) => e.visibility === 'patient_visible' &&
             (e.entry_type === 'instruction' ||
              (e.entry_type === 'patient_message' && e.metadata?.direction === 'outgoing')) &&
             // See lib/patient_visibility.ts — a correctness filter over an
             // already-enforced database boundary, not a security control.
             isApprovedForPatient(e)
    );

    // Helper to get author display name
    const getAuthorLabel = (entry: TimelineEntry) => {
      if (entry.author?.display_name) {
        if (entry.author_role === 'clinician') {
          // Extract last name for "Dr. LastName" format
          const nameParts = entry.author.display_name.split(' ');
          const lastName = nameParts[nameParts.length - 1];
          return `Dr. ${lastName}`;
        }
        return entry.author.display_name;
      }
      return entry.author_role === 'clinician' ? 'Your Doctor' : 'Care Team';
    };

    return (
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 p-4 sm:p-6 lg:p-8 h-full overflow-auto">
        {/* Left column: Care Timeline */}
        <div className="space-y-4">
          <SunshineBlock
            glanceCache={careNote.glance_cache}
            highlights={[]}
            entries={careInstructions}
            userRole="patient"
          />
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-secondary flex items-center justify-center">
              <Heart className="w-5 h-5 text-muted-foreground" />
            </div>
            <div>
              <h2 className="text-lg font-semibold heading-display">Your Care Timeline</h2>
              <p className="text-xs text-muted-foreground">Messages and instructions from your care team</p>
            </div>
          </div>

          <Card>
            <CardContent className="pt-5 space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold">Care Instructions</h3>
                  <p className="text-xs text-muted-foreground">From your clinician or staff</p>
                </div>
              </div>
              {careInstructions.map((entry) => (
                <div key={entry.id} className="p-4 rounded-lg border border-border bg-card">
                  <div className="flex items-center gap-2 mb-2">
                    <Badge variant="outline" className="text-xs">
                      {getAuthorLabel(entry)}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {new Date(entry.created_at).toLocaleDateString()}
                    </span>
                  </div>
                  {/* The clinician timeline has rendered this since retraction
                      shipped; the patient card did not — which inverted the
                      whole point. The person who needs to know a dose was
                      withdrawn is the person who was told to take it. */}
                  {entry.is_retracted && (
                    <div className="mb-2 rounded-md border-2 border-red-600 bg-red-50 dark:bg-red-950/40 px-3 py-2">
                      <span className="inline-block rounded bg-red-600 px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide text-white">
                        [WITHDRAWN BY CARE TEAM]
                      </span>
                      <p className="mt-1.5 text-sm font-medium text-red-700 dark:text-red-400">
                        Do not follow this message. {entry.retraction_reason
                          ? `Reason: ${entry.retraction_reason}`
                          : 'Your care team will contact you with a correction.'}
                      </p>
                    </div>
                  )}
                  <p
                    className={
                      entry.is_retracted
                        ? 'text-sm leading-relaxed line-through decoration-2 decoration-red-600 opacity-70'
                        : 'text-sm leading-relaxed'
                    }
                  >
                    {entry.content_text}
                  </p>
                </div>
              ))}
              {careInstructions.length === 0 && (
                <p className="text-muted-foreground text-sm py-6 text-center">
                  No instructions yet.
                </p>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Right column: Send Update */}
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center">
              <Send className="w-5 h-5 text-primary" />
            </div>
            <div>
              <h2 className="text-lg font-semibold heading-display">Send an Update</h2>
              <p className="text-xs text-muted-foreground">Share symptoms or questions with your care team</p>
            </div>
          </div>

          {token && (
            <VoiceCapture
              token={token}
              userRole="patient"
              careNoteId={careNote.id}
              onSummary={handleVoiceSummary}
            />
          )}

          <Card className="h-fit">
            <CardContent className="pt-5 space-y-4">
              <p className="text-sm text-muted-foreground">
                Let your care team know how you&apos;re feeling, report any new symptoms,
                or ask questions about your treatment.
              </p>
              <textarea
                value={draftMessage}
                onChange={(e) => { setDraftMessage(e.target.value); setGateBlock(null); }}
                className="w-full min-h-[180px] p-3 bg-secondary/50 border border-border rounded-lg text-sm leading-relaxed focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/40 transition-all resize-y"
                placeholder="Example: I've been feeling dizzy in the mornings, and my appetite has decreased since last week..."
              />
              <div className="flex justify-end">
                <Button
                  className="gap-2"
                  onClick={handleSendPatientUpdate}
                  disabled={sendingMessage || !draftMessage.trim()}
                >
                  {sendingMessage ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Send className="w-4 h-4" />
                  )}
                  {sendingMessage ? 'Sending...' : 'Send to Care Team'}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    );
  }

  // Admin view - 3 column layout matching clinician view
  if (activeRole === 'admin') {
    const carePlanItems = careNote.glance_cache.care_plan_items || [];
    const carePlanScore = careNote.glance_cache.care_plan_score || 0;

    return (
      <div className="flex flex-col xl:grid xl:grid-cols-12 gap-4 p-4 h-full overflow-auto xl:overflow-hidden">
        {/* Left column: At a Glance */}
        <div className="xl:col-span-3 overflow-visible xl:overflow-auto space-y-3">
          <SunshineBlock
            glanceCache={careNote.glance_cache}
            highlights={highlights}
            entries={entries}
            userRole="admin"
            conflictCount={conflictCount}
            onReviewConflicts={handleReviewConflicts}
          />
          <TopCard
            aiDegraded={conflictsDegraded !== null}
            currentNoteVersion={careNote.version}
            glanceCache={careNote.glance_cache}
            highlights={highlights}
            changesSinceLastVisit={careNote.glance_cache.changes_since_last_visit || []}
            carePlanItems={[]} /* Care plan moved to center column */
            carePlanScore={carePlanScore}
            userRole="admin"
            onHighlightClick={handleHighlightClick}
            onAcceptHighlight={() => {}}
            onRejectHighlight={() => {}}
            onToggleCarePlanItem={() => {}}
          />
        </div>

        {/* Center column: Care Plan */}
        <div className="xl:col-span-5 overflow-visible xl:overflow-auto">
          <Card>
            <CardContent className="pt-4 pb-3">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-semibold">Care Plan</h3>
                <Badge
                  variant="secondary"
                  className={`text-xs ${carePlanScore >= 50 ? 'bg-primary/10 text-primary' : 'bg-red-50 text-red-600'}`}
                >
                  {carePlanScore}%
                </Badge>
              </div>
              {/* Progress bar */}
              <div className="w-full bg-secondary rounded-full h-2 mb-4 overflow-hidden">
                <div
                  className={`h-2 rounded-full transition-all duration-700 ${
                    carePlanScore >= 50 ? 'bg-primary' : 'bg-red-500'
                  }`}
                  style={{ width: `${Math.min(carePlanScore, 100)}%` }}
                />
              </div>
              <div className="space-y-2">
                {carePlanItems.map((item, idx) => (
                  <div
                    key={idx}
                    className="flex items-center gap-3 text-sm p-2 rounded-lg bg-secondary/30"
                  >
                    <div className={`w-4 h-4 rounded border-2 flex items-center justify-center shrink-0 ${
                      item.completed
                        ? 'bg-primary border-primary'
                        : 'border-red-400 bg-red-50'
                    }`}>
                      {item.completed && (
                        <svg className="w-2.5 h-2.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                        </svg>
                      )}
                    </div>
                    <span className={item.completed ? 'line-through text-muted-foreground' : 'text-foreground'}>
                      {item.label}
                    </span>
                  </div>
                ))}
                {carePlanItems.length === 0 && (
                  <p className="text-sm text-muted-foreground py-4 text-center">No care plan items yet.</p>
                )}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Right column: Timeline */}
        <div className="xl:col-span-4 overflow-visible xl:overflow-hidden min-h-[400px]">
          <TimelineView
            entries={entries}
            comments={comments}
            userRole="admin"
            onAddComment={() => {}}
            onNavigateToSource={handleNavigateToSource}
            commentingEntryId={null}
            currentUser={currentUser}
            onSubmitComment={handleSubmitComment}
            onResolveComment={handleResolveComment}
            clinicMembers={clinicMembers}
          />
        </div>
      </div>
    );
  }

  // Clinician/Staff view — 3-column layout
  return (
    <div className="flex flex-col h-full overflow-hidden">
      <DeferReasonDialog
        open={deferTarget !== null}
        riskLevel={deferTarget?.risk_level ?? 'high'}
        snippet={deferTarget?.content_snippet}
        onCancel={() => setDeferTarget(null)}
        onConfirm={confirmDefer}
      />

      {/* Withdrawal reason. Reuses the deferral dialog rather than a second
          implementation: it already enforces a minimum length, clears between
          uses, and — unlike window.prompt — cannot be suppressed by the browser
          into a silent no-op. The reason is shown to the patient verbatim. */}
      <DeferReasonDialog
        open={retractTarget !== null}
        riskLevel="critical"
        snippet={
          entries.find((e) => e.id === retractTarget)?.content_text?.slice(0, 160)
        }
        onCancel={() => setRetractTarget(null)}
        onConfirm={confirmRetraction}
      />
      {/* Draft message panel */}
      {showMessageDraft && (
        <div className="p-3 border-b border-border bg-card shrink-0">
          <Card className="max-w-3xl mx-auto">
            <CardContent className="p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <MessageSquare className="w-4 h-4 text-primary" />
                  <h3 className="text-sm font-semibold">Draft Patient Message</h3>
                </div>
                <button
                  onClick={() => { setShowMessageDraft(false); setDraftMessage(''); setDraftKeyPoints([]); setGateBlock(null); }}
                  className="text-muted-foreground hover:text-foreground"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              {generatingDraft ? (
                <div className="flex items-center justify-center py-6 gap-2 text-sm text-muted-foreground">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  AI is drafting a message...
                </div>
              ) : (
                <>
                  <textarea
                    value={draftMessage}
                    onChange={(e) => setDraftMessage(e.target.value)}
                    className="w-full min-h-[100px] p-3 bg-secondary/50 border border-border rounded-lg text-sm leading-relaxed focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/40 transition-all resize-y"
                    placeholder="Edit the draft message before sending..."
                  />
                  {gateBlock && (
                    <div
                      role="alert"
                      className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 space-y-2"
                    >
                      <div className="flex items-start gap-2">
                        <AlertTriangle className="w-4 h-4 text-destructive shrink-0 mt-0.5" />
                        <div className="space-y-1">
                          <p className="text-xs font-semibold text-destructive">
                            Not sent &mdash; nothing was written to the patient&apos;s record
                          </p>
                          <p className="text-xs text-muted-foreground leading-relaxed">
                            {gateBlock.message}
                          </p>
                        </div>
                      </div>

                      {gateBlock.ungroundedTerms.length > 0 && (
                        <div className="space-y-1 pl-6">
                          <p className="text-[11px] font-medium text-muted-foreground">
                            Not found anywhere in this patient&apos;s record:
                          </p>
                          <div className="flex flex-wrap gap-1.5">
                            {gateBlock.ungroundedTerms.map((t) => (
                              <code
                                key={t}
                                className="px-1.5 py-0.5 rounded bg-destructive/15 text-destructive text-[11px] font-mono"
                              >
                                {t}
                              </code>
                            ))}
                          </div>
                          <p className="text-[11px] text-muted-foreground">
                            A dose or figure that appears here but not in the record may have been
                            mistyped or invented. Correct it above and send again.
                          </p>
                        </div>
                      )}

                      {gateBlock.prohibitedHits.length > 0 && (
                        <div className="space-y-1 pl-6">
                          <p className="text-[11px] font-medium text-muted-foreground">
                            Needs to come from you directly, not a drafted message:
                          </p>
                          <div className="flex flex-wrap gap-1.5">
                            {gateBlock.prohibitedHits.map((h) => (
                              <code
                                key={h}
                                className="px-1.5 py-0.5 rounded bg-destructive/15 text-destructive text-[11px] font-mono"
                              >
                                {h.replace(/_/g, ' ')}
                              </code>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {draftKeyPoints.length > 0 && (
                    <div className="space-y-1">
                      <p className="text-xs font-medium text-muted-foreground">Key points (reference):</p>
                      <ul className="text-xs text-muted-foreground space-y-0.5">
                        {draftKeyPoints.map((kp, i) => (
                          <li key={i} className="flex items-start gap-1.5">
                            <span className="text-primary mt-0.5">&#8226;</span>
                            {kp}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <div className="flex items-center gap-2 justify-end">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => { setShowMessageDraft(false); setDraftMessage(''); setDraftKeyPoints([]); setGateBlock(null); }}
                    >
                      Cancel
                    </Button>
                    <Button
                      size="sm"
                      className="gap-2"
                      onClick={handleSendPatientMessage}
                      disabled={sendingMessage || !draftMessage.trim()}
                    >
                      {sendingMessage ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Send className="w-3.5 h-3.5" />
                      )}
                      {sendingMessage ? 'Sending...' : 'Send to Patient'}
                    </Button>
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      <div className="flex flex-col xl:grid xl:grid-cols-12 gap-4 p-4 flex-1 overflow-auto">
        {/* Left column: At a Glance (col-span-3) */}
        <div className="xl:col-span-3 space-y-3">
          {/* An outage has to be visible here, not silent. If the contradiction check
              could not run, "no contradictions" below is an absence of evidence, not
              evidence of absence — and a clinician reading a clean Glance View has no
              way to tell the difference unless told. */}
          {conflictsDegraded && (
            <DegradedAIPanel
              kind={conflictsDegraded}
              findings={offlineFindings}
              coverageNote={offlineCoverageNote(entries)}
              onRetry={() => { setConflictsDegraded(null); setConflictsChecked(false); }}
            />
          )}

          {/* Sunshine disclosure sits above everything: open actions, how much
              of this is AI, and whether it is auditable — before any content. */}
          <SunshineBlock
            glanceCache={careNote.glance_cache}
            highlights={highlights}
            entries={entries}
            userRole={activeRole}
            conflictCount={conflictCount}
            onReviewConflicts={handleReviewConflicts}
          />
          <TopCard
            aiDegraded={conflictsDegraded !== null}
            currentNoteVersion={careNote.version}
            glanceCache={careNote.glance_cache}
            highlights={highlights}
            changesSinceLastVisit={careNote.glance_cache.changes_since_last_visit || []}
            carePlanItems={[]} /* Care plan moved to center column */
            carePlanScore={careNote.glance_cache.care_plan_score || 0}
            userRole={activeRole}
            onHighlightClick={handleHighlightClick}
            onAcceptHighlight={handleAcceptHighlight}
            onRejectHighlight={handleRejectHighlight}
            loadingAction={loadingAction}
            onToggleCarePlanItem={handleToggleCarePlanItem}
            conflictCount={conflictCount}
            hasCriticalConflict={hasCriticalConflict(conflicts)}
            onReviewConflicts={handleReviewConflicts}
            onAssignAction={handleAssignAction}
            onCompleteAction={handleCompleteAction}
            onDeferAction={handleDeferAction}
          />
        </div>

        {/* Center column: Editor + Care Plan / AI Actions (col-span-5) */}
        <div className="xl:col-span-5 flex flex-col gap-3">
          {/* Care Note Editor */}
          <div>
            {currentUser && (
              <CareNoteEditor
                careNoteId={careNote.id}
                currentUser={currentUser}
                token={token}
                // The shared care-note document is the clinician's section.
                // Staff contribute through timeline notes, comments, action
                // assignment and ambient capture — not by overwriting it, and
                // admins have oversight rather than edit rights (the same split
                // the collab server enforces at the protocol level).
                //
                // RLS already rejects the write; this makes the boundary
                // visible instead of letting staff type into a field whose save
                // will be refused.
                readOnly={activeRole !== 'clinician'}
                onCreateTimelineEntry={handleCreateTimelineEntry}
              />
            )}
          </div>

          {/* 2-column layout below editor: Care Plan | AI Actions */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {/* Care Plan */}
            <CarePlanCard
              items={careNote.glance_cache.care_plan_items || []}
              score={careNote.glance_cache.care_plan_score || 0}
              onToggleItem={handleToggleCarePlanItem}
            />

            {/* Ambient capture — clinical view only, per the brief. */}
            {(activeRole === 'clinician' || activeRole === 'staff') && token && (
              <div className="sm:col-span-2">
                <VoiceCapture
                  token={token}
                  userRole={activeRole}
                  careNoteId={careNote.id}
                  onSummary={handleVoiceSummary}
                />
              </div>
            )}

            {/* AI Actions */}
            {(activeRole === 'clinician' || activeRole === 'staff') && (
              <AIActionsCard
                canGenerateSummary={activeRole === 'clinician'}
                onGenerateSummary={handleRequestAISummary}
                summarising={loadingAction === 'ai-summary'}
                onDraftMessage={handleDraftPatientMessage}
                drafting={generatingDraft}
              />
            )}
          </div>
        </div>

        {/* Right column: Timeline (col-span-4) */}
        <div className="xl:col-span-4 overflow-visible xl:overflow-hidden min-h-[400px]">
          <TimelineView
            entries={entries}
            comments={comments}
            userRole={activeRole}
            onAddComment={handleAddComment}
            onNavigateToSource={handleNavigateToSource}
            // Withdrawal is a clinician speech act, matching who may approve a
            // send. Staff cannot retract advice they were not permitted to issue.
            onRetract={
              currentUser?.role === 'clinician' || currentUser?.role === 'admin'
                ? handleRetractMessage
                : undefined
            }
            commentingEntryId={commentingEntryId}
            currentUser={currentUser}
            onSubmitComment={handleSubmitComment}
            onResolveComment={handleResolveComment}
            clinicMembers={clinicMembers}
          />
        </div>
      </div>
    </div>
  );
}
