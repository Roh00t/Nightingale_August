'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { useParams } from 'next/navigation';
import dynamic from 'next/dynamic';
import { createClient } from '@/lib/supabase/client';
import { TopCard } from '@/components/glance/TopCard';
import { TimelineView } from '@/components/timeline/TimelineView';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { useAppStore } from '@/lib/stores/app-store';
import { toast } from 'sonner';
import type {
  CareNote,
  TimelineEntry,
  Comment,
  Highlight,
  Profile,
  UserRole,
  AISummarizeResponse,
  ChangeSinceLastVisit,
  CarePlanItem,
} from '@/lib/types';
import { Sparkles, FileText, Heart, Loader2, MessageSquare, Send, X } from 'lucide-react';

const CareNoteEditor = dynamic(
  () => import('@/components/editor/CareNoteEditor').then((mod) => ({ default: mod.CareNoteEditor })),
  {
    ssr: false,
    loading: () => <Skeleton className="h-full rounded-lg" />,
  }
);

export default function PatientCareNotePage() {
  const params = useParams();
  const patientId = params.id as string;
  const supabase = createClient();
  const { currentUser, setHighlightedEntryId } = useAppStore();
  const activeRole = (currentUser?.role || 'clinician') as UserRole;

  const [careNote, setCareNote] = useState<CareNote | null>(null);
  const [entries, setEntries] = useState<TimelineEntry[]>([]);
  const [comments, setComments] = useState<Comment[]>([]);
  const [highlights, setHighlights] = useState<Highlight[]>([]);
  const [loading, setLoading] = useState(true);
  const [token, setToken] = useState<string>('');
  const [commentingEntryId, setCommentingEntryId] = useState<string | null>(null);
  const [loadingAction, setLoadingAction] = useState<string | null>(null);
  const [showMessageDraft, setShowMessageDraft] = useState(false);
  const [draftMessage, setDraftMessage] = useState('');
  const [draftKeyPoints, setDraftKeyPoints] = useState<string[]>([]);
  const [generatingDraft, setGeneratingDraft] = useState(false);
  const [sendingMessage, setSendingMessage] = useState(false);
  const [clinicMembers, setClinicMembers] = useState<Profile[]>([]);

  useEffect(() => {
    let channel: ReturnType<typeof supabase.channel> | null = null;

    async function loadData() {
      const { data: { session } } = await supabase.auth.getSession();
      if (session) setToken(session.access_token);

      const { data: noteData } = await supabase
        .from('care_notes')
        .select('*')
        .eq('patient_id', patientId)
        .single();

      if (noteData) {
        setCareNote(noteData as CareNote);

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
  }, [patientId, supabase]);

  const handleHighlightClick = useCallback((highlightId: string, sourceEntryId: string | null) => {
    if (sourceEntryId) {
      setHighlightedEntryId(sourceEntryId);
      const element = document.getElementById(`entry-${sourceEntryId}`);
      if (element) {
        element.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
      setTimeout(() => setHighlightedEntryId(null), 4000);
    }
  }, [setHighlightedEntryId]);

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

    const updatedCache = {
      ...careNote.glance_cache,
      care_plan_items: items,
      care_plan_score: newScore,
    };

    // Optimistically update local state
    setCareNote((prev) => prev ? { ...prev, glance_cache: updatedCache } : prev);

    // Persist to Supabase
    const { error } = await supabase
      .from('care_notes')
      .update({ glance_cache: updatedCache })
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
      const response = await fetch(`${process.env.NEXT_PUBLIC_AI_SERVICE_URL || 'http://localhost:8000'}/api/ai/draft-patient-message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          care_note_id: careNote.id,
          entries: entries.map((e) => ({
            entry_id: e.id,
            content: e.content_text || '',
            entry_type: e.entry_type || 'note',
            created_at: e.created_at,
          })),
          author_role: currentUser.role,
        }),
      });

      if (response.ok) {
        const data = await response.json();
        setDraftMessage(data.draft_message || '');
        setDraftKeyPoints(data.key_points || []);
      } else {
        const errorBody = await response.json().catch(() => null);
        toast.error(errorBody?.detail || 'Failed to generate message draft');
        setShowMessageDraft(false);
      }
    } catch {
      toast.error('AI service unavailable');
      setShowMessageDraft(false);
    } finally {
      setGeneratingDraft(false);
    }
  }, [careNote, entries, currentUser]);

  const handleSendPatientMessage = useCallback(async () => {
    if (!careNote || !currentUser || !draftMessage.trim()) return;

    setSendingMessage(true);
    try {
      const { data: { user: authUser } } = await supabase.auth.getUser();
      if (!authUser) {
        toast.error('Session expired. Please refresh the page.');
        return;
      }

      // Clinicians and staff send "instruction" type entries to patients
      const { error: insertError } = await supabase
        .from('timeline_entries')
        .insert({
          care_note_id: careNote.id,
          entry_type: 'instruction',
          author_role: currentUser.role,
          author_id: authUser.id,
          content: {
            type: 'doc',
            content: [{ type: 'paragraph', content: [{ type: 'text', text: draftMessage }] }],
          },
          content_text: draftMessage,
          risk_level: 'info',
          visibility: 'patient_visible',
          metadata: {
            direction: 'outgoing',
            ai_drafted: true,
          },
        });

      if (insertError) {
        console.error('Failed to send patient message:', insertError);
        toast.error('Failed to send message');
        return;
      }

      // Fetch the entry we just created
      const { data: newEntry } = await supabase
        .from('timeline_entries')
        .select('*, author:profiles!timeline_entries_author_profile_fkey(*)')
        .eq('care_note_id', careNote.id)
        .eq('author_id', authUser.id)
        .eq('entry_type', 'instruction')
        .order('created_at', { ascending: false })
        .limit(1)
        .single();

      if (newEntry) {
        setEntries((prev) => [newEntry as TimelineEntry, ...prev]);
      }

      setShowMessageDraft(false);
      setDraftMessage('');
      setDraftKeyPoints([]);
      toast.success('Care instructions sent to patient');
    } catch {
      toast.error('Failed to send message');
    } finally {
      setSendingMessage(false);
    }
  }, [careNote, currentUser, draftMessage, supabase]);

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
      const response = await fetch(`${process.env.NEXT_PUBLIC_AI_SERVICE_URL || 'http://localhost:8000'}/api/ai/summarize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          care_note_id: careNote.id,
          entries: entries.map((e) => ({
            entry_id: e.id,
            content: e.content_text || '',
            entry_type: e.entry_type || 'note',
            created_at: e.created_at,
          })),
        }),
      });

      if (response.ok) {
        const data: AISummarizeResponse = await response.json();

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
              ...careNote.glance_cache,
              care_plan_score: computedScore,
              changes_since_last_visit: transformedChanges,
              care_plan_items: mergedCarePlanItems,
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
      } else {
        const errorBody = await response.json().catch(() => null);
        console.error('Summarize failed:', response.status, errorBody);
        toast.error(errorBody?.detail?.[0]?.msg || 'Failed to generate summary');
      }
    } catch {
      toast.error('AI service unavailable');
    } finally {
      setLoadingAction(null);
    }
  }, [careNote, entries, supabase, currentUser]);

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
              (e.entry_type === 'patient_message' && e.metadata?.direction === 'outgoing'))
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
                  <p className="text-sm leading-relaxed">{entry.content_text}</p>
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

          <Card className="h-fit">
            <CardContent className="pt-5 space-y-4">
              <p className="text-sm text-muted-foreground">
                Let your care team know how you&apos;re feeling, report any new symptoms,
                or ask questions about your treatment.
              </p>
              <textarea
                value={draftMessage}
                onChange={(e) => setDraftMessage(e.target.value)}
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
        <div className="xl:col-span-3 overflow-visible xl:overflow-auto">
          <TopCard
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
                  onClick={() => { setShowMessageDraft(false); setDraftMessage(''); setDraftKeyPoints([]); }}
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
                      onClick={() => { setShowMessageDraft(false); setDraftMessage(''); setDraftKeyPoints([]); }}
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
        <div className="xl:col-span-3">
          <TopCard
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
                readOnly={false}
                onCreateTimelineEntry={handleCreateTimelineEntry}
              />
            )}
          </div>

          {/* 2-column layout below editor: Care Plan | AI Actions */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {/* Care Plan */}
            <Card>
              <CardContent className="pt-4 pb-3">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-semibold">Care Plan</h3>
                  <Badge
                    variant="secondary"
                    className={`text-xs ${(careNote.glance_cache.care_plan_score || 0) >= 50 ? 'bg-primary/10 text-primary' : 'bg-red-50 text-red-600'}`}
                  >
                    {careNote.glance_cache.care_plan_score || 0}%
                  </Badge>
                </div>
                {/* Progress bar */}
                <div className="w-full bg-secondary rounded-full h-1.5 mb-3 overflow-hidden">
                  <div
                    className={`h-1.5 rounded-full transition-all duration-700 ${
                      (careNote.glance_cache.care_plan_score || 0) >= 50 ? 'bg-primary' : 'bg-red-500'
                    }`}
                    style={{ width: `${Math.min(careNote.glance_cache.care_plan_score || 0, 100)}%` }}
                  />
                </div>
                <div className="space-y-1.5">
                  {(careNote.glance_cache.care_plan_items || []).map((item, idx) => (
                    <div
                      key={idx}
                      className="flex items-center gap-2 text-xs cursor-pointer hover:bg-secondary/50 rounded p-1.5 -mx-1"
                      onClick={() => handleToggleCarePlanItem(idx)}
                    >
                      <div className={`w-3.5 h-3.5 rounded border-2 flex items-center justify-center shrink-0 ${
                        item.completed ? 'bg-primary border-primary' : 'border-red-400 bg-red-50'
                      }`}>
                        {item.completed && (
                          <svg className="w-2.5 h-2.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                          </svg>
                        )}
                      </div>
                      <span className={item.completed ? 'line-through text-muted-foreground' : ''}>
                        {item.label}
                      </span>
                    </div>
                  ))}
                  {(careNote.glance_cache.care_plan_items || []).length === 0 && (
                    <p className="text-xs text-muted-foreground py-2">No care plan items yet.</p>
                  )}
                </div>
              </CardContent>
            </Card>

            {/* AI Actions */}
            {(activeRole === 'clinician' || activeRole === 'staff') && (
              <Card className="self-start">
                <CardContent className="pt-4 pb-3 space-y-2">
                  <h3 className="text-sm font-semibold mb-3">AI Actions</h3>
                  {activeRole === 'clinician' && (
                    <Button
                      size="sm"
                      className="w-full text-xs gap-2"
                      onClick={handleRequestAISummary}
                      disabled={loadingAction === 'ai-summary'}
                    >
                      {loadingAction === 'ai-summary' ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Sparkles className="w-3.5 h-3.5" />
                      )}
                      {loadingAction === 'ai-summary' ? 'Generating...' : 'Generate AI Summary'}
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    className="w-full text-xs gap-2"
                    onClick={handleDraftPatientMessage}
                    disabled={generatingDraft}
                  >
                    {generatingDraft ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <MessageSquare className="w-3.5 h-3.5" />
                    )}
                    {generatingDraft ? 'Drafting...' : 'Message Patient'}
                  </Button>
                </CardContent>
              </Card>
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
