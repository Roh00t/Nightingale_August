'use client';

import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { diffLines } from 'diff';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Collaboration from '@tiptap/extension-collaboration';
import CollaborationCursor from '@tiptap/extension-collaboration-cursor';
import Highlight from '@tiptap/extension-highlight';
import Placeholder from '@tiptap/extension-placeholder';
import * as Y from 'yjs';
import { HocuspocusProvider } from '@hocuspocus/provider';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { getRoleColor } from '@/lib/utils';
import type { UserRole, Profile } from '@/lib/types';
import {
  Bold,
  Italic,
  List,
  Heading2,
  Heading3,
  FileEdit,
  Wifi,
  WifiOff,
  Loader2,
  Save,
  CheckCircle2,
  Eye,
  History,
} from 'lucide-react';
import { createClient } from '@/lib/supabase/client';
import { toast } from 'sonner';
import { SaveConfirmDialog } from './SaveConfirmDialog';
import { VersionHistoryModal } from './VersionHistoryModal';

interface CareNoteEditorProps {
  careNoteId: string;
  currentUser: Profile;
  token: string;
  readOnly?: boolean;
  onCreateTimelineEntry?: (contentJson: Record<string, unknown>, contentText: string) => Promise<void>;
}

// Helper functions for encoding/decoding large Yjs states
function encodeYjsStateToBase64(state: Uint8Array): string {
  // Convert Uint8Array to binary string in chunks to avoid call stack size limits
  const chunkSize = 8192; // Safe chunk size
  let binaryString = '';
  for (let i = 0; i < state.length; i += chunkSize) {
    const chunk = state.subarray(i, Math.min(i + chunkSize, state.length));
    binaryString += String.fromCharCode(...chunk);
  }
  return btoa(binaryString);
}

function decodeBase64ToYjsState(encoded: string): Uint8Array {
  if (!encoded || typeof encoded !== 'string') {
    return new Uint8Array(0);
  }

  // Handle hex-encoded bytea from PostgreSQL (e.g. "\x0a0b...")
  if (encoded.startsWith('\\x') || encoded.startsWith('\\X') ||
      encoded.startsWith('\x5cx') || encoded.startsWith('\x5cX')) {
    try {
      const hex = encoded.replace(/^\\[xX]/, '');
      const bytes = new Uint8Array(hex.length / 2);
      for (let i = 0; i < hex.length; i += 2) {
        bytes[i / 2] = parseInt(hex.substring(i, i + 2), 16);
      }
      return bytes;
    } catch {
      console.warn('[CareNoteEditor] Invalid hex yjs_state, starting fresh');
      return new Uint8Array(0);
    }
  }

  // Validate base64 characters before attempting decode
  if (!/^[A-Za-z0-9+/\n\r]*={0,2}$/.test(encoded)) {
    console.warn('[CareNoteEditor] yjs_state is not valid base64, starting fresh');
    return new Uint8Array(0);
  }

  try {
    const binaryString = atob(encoded);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    return bytes;
  } catch {
    console.warn('[CareNoteEditor] Failed to decode base64 yjs_state, starting fresh');
    return new Uint8Array(0);
  }
}

export function CareNoteEditor({
  careNoteId,
  currentUser,
  token,
  readOnly = false,
  onCreateTimelineEntry,
}: CareNoteEditorProps) {
  const supabase = createClient();
  const [ydoc] = useState(() => new Y.Doc());
  const [provider, setProvider] = useState<HocuspocusProvider | null>(null);
  const [status, setStatus] = useState<'connecting' | 'connected' | 'disconnected' | 'unavailable'>('connecting');
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [fallbackLoaded, setFallbackLoaded] = useState(false);
  const [connectedUsers, setConnectedUsers] = useState<Array<{
    name: string;
    role: string;
    color: string;
    section?: string;
  }>>([]);
  const [baselineContent, setBaselineContent] = useState<{
    text: string;
    json: Record<string, unknown>;
    timestamp: string;
  } | null>(null);
  const [showSaveDialog, setShowSaveDialog] = useState(false);
  const [diffData, setDiffData] = useState<{
    additions: any[];
    deletions: any[];
    unchanged: any[];
  } | null>(null);
  const [showLivePreview, setShowLivePreview] = useState(false);
  const [liveDiff, setLiveDiff] = useState<{
    additions: number;
    deletions: number;
  } | null>(null);
  const [showVersionHistory, setShowVersionHistory] = useState(false);

  useEffect(() => {
    const collabUrl = process.env.NEXT_PUBLIC_COLLAB_URL || 'ws://localhost:1234';
    const hocuspocusProvider = new HocuspocusProvider({
      url: collabUrl,
      name: `care-note:${careNoteId}`,
      document: ydoc,
      token,
      onConnect: () => setStatus('connected'),
      onDisconnect: () => setStatus('disconnected'),
      onAwarenessUpdate: ({ states }) => {
        const users = Array.from(states.values())
          .filter((s: Record<string, unknown>) => s.user)
          .map((s: Record<string, unknown>) => s.user as { name: string; role: string; color: string; section?: string });
        setConnectedUsers(users);
      },
    });

    setProvider(hocuspocusProvider);

    return () => {
      hocuspocusProvider.destroy();
    };
  }, [careNoteId, token, ydoc]);

  // Connection timeout: switch to 'unavailable' if no connection after 5 seconds
  useEffect(() => {
    if (status !== 'connecting') return;

    const timeout = setTimeout(() => {
      setStatus('unavailable');
    }, 5000);

    return () => clearTimeout(timeout);
  }, [status]);

  // Fallback: load Yjs state from Supabase when collab server is disconnected or unavailable
  useEffect(() => {
    if ((status !== 'disconnected' && status !== 'unavailable') || fallbackLoaded) return;

    async function loadFallback() {
      const { data, error } = await supabase
        .from('care_notes')
        .select('yjs_state')
        .eq('id', careNoteId)
        .single();

      if (error || !data?.yjs_state) return;

      try {
        const bytes = decodeBase64ToYjsState(data.yjs_state);
        Y.applyUpdate(ydoc, bytes);
      } catch (err) {
        console.error('[CareNoteEditor] Failed to decode fallback yjs_state:', err);
      }
      setFallbackLoaded(true);
    }

    loadFallback();
  }, [status, fallbackLoaded, careNoteId, supabase, ydoc]);

  // Initialize editor - MUST come before any useEffects that depend on editor
  const editor = useEditor({
    editable: !readOnly,
    extensions: [
      StarterKit.configure({
        history: false,
      }),
      Highlight.configure({
        multicolor: true,
        HTMLAttributes: {
          class: 'highlight-mark',
        },
      }),
      Placeholder.configure({
        placeholder: readOnly
          ? ''
          : 'Start writing the care note...',
      }),
      ...(provider
        ? [
            Collaboration.configure({
              document: ydoc,
            }),
            CollaborationCursor.configure({
              provider,
              user: {
                name: currentUser.display_name,
                role: currentUser.role,
                color: getRoleColor(currentUser.role as UserRole),
              },
            }),
          ]
        : []),
    ],
    editorProps: {
      attributes: {
        class: 'prose prose-sm text-sm max-w-none focus:outline-none min-h-[200px] p-4',
      },
    },
  }, [provider]);

  // Capture baseline after initial sync with localStorage persistence
  useEffect(() => {
    if (!editor || !provider || status !== 'connected') return;

    const captureBaseline = () => {
      const text = editor.getText();
      const json = editor.getJSON();
      const baseline = {
        text,
        json,
        timestamp: new Date().toISOString(),
      };

      // Save to state
      setBaselineContent(baseline);

      // Persist to localStorage
      try {
        localStorage.setItem(`baseline_${careNoteId}`, JSON.stringify(baseline));
      } catch (error) {
        console.error('Failed to save baseline to localStorage:', error);
      }
    };

    // Try to restore from localStorage first
    const restoreBaseline = () => {
      try {
        const stored = localStorage.getItem(`baseline_${careNoteId}`);
        if (stored) {
          const baseline = JSON.parse(stored);
          // Check if baseline is less than 24 hours old
          const age = Date.now() - new Date(baseline.timestamp).getTime();
          const DAY = 24 * 60 * 60 * 1000;

          if (age < DAY) {
            setBaselineContent(baseline);
            return true; // Successfully restored
          } else {
            // Clear old baseline
            localStorage.removeItem(`baseline_${careNoteId}`);
          }
        }
      } catch (error) {
        console.error('Failed to restore baseline:', error);
      }
      return false;
    };

    // If not already set and can't restore, capture new baseline
    if (!baselineContent) {
      const restored = restoreBaseline();
      if (!restored) {
        setTimeout(captureBaseline, 500);
      }
    }
  }, [editor, provider, status, baselineContent, careNoteId]);

  // Listen for save events from other users via awareness
  useEffect(() => {
    if (!provider?.awareness || !currentUser) return;

    const handleAwarenessChange = () => {
      const states = provider.awareness!.getStates();
      states.forEach((state: any) => {
        if (state.saveEvent && state.saveEvent.userId !== currentUser.id) {
          const { userName } = state.saveEvent;

          toast.info(`${userName} saved changes to this note`, {
            action: {
              label: 'Refresh',
              onClick: () => {
                if (editor) {
                  setBaselineContent({
                    text: editor.getText(),
                    json: editor.getJSON(),
                    timestamp: new Date().toISOString(),
                  });
                  toast.success('Baseline refreshed');
                }
              },
            },
          });
        }
      });
    };

    provider.awareness.on('change', handleAwarenessChange);
    return () => provider.awareness!.off('change', handleAwarenessChange);
  }, [provider, currentUser, editor]);

  // Debounced live diff computation
  useEffect(() => {
    if (!editor || !baselineContent || !showLivePreview) {
      setLiveDiff(null);
      return;
    }

    const computeLiveDiff = () => {
      const currentText = editor.getText();
      const changes = diffLines(baselineContent.text, currentText);

      const additionCount = changes
        .filter(c => c.added)
        .reduce((sum, c) => sum + (c.count || 0), 0);

      const deletionCount = changes
        .filter(c => c.removed)
        .reduce((sum, c) => sum + (c.count || 0), 0);

      setLiveDiff({ additions: additionCount, deletions: deletionCount });
    };

    // Debounce to avoid excessive computation
    const timeout = setTimeout(computeLiveDiff, 500);
    return () => clearTimeout(timeout);
  }, [editor?.state.doc, baselineContent, showLivePreview]);

  // Manual save handler - shows diff dialog
  const handleSave = useCallback(async () => {
    if (!editor || !baselineContent) {
      toast.error('Editor not ready');
      return;
    }

    const currentText = editor.getText();
    const hasChanges = currentText.trim() !== baselineContent.text.trim();

    if (!hasChanges) {
      toast.info('No changes to save');
      return;
    }

    // Compute diff
    const changes = diffLines(baselineContent.text, currentText);
    const diffResult = {
      additions: changes.filter(c => c.added),
      deletions: changes.filter(c => c.removed),
      unchanged: changes.filter(c => !c.added && !c.removed),
    };

    setDiffData(diffResult);
    setShowSaveDialog(true);
  }, [editor, baselineContent]);

  const handleConfirmSave = useCallback(async () => {
    if (!editor) return;

    setShowSaveDialog(false);
    setSaveStatus('saving');

    try {
      // 1. Save Yjs state
      const state = Y.encodeStateAsUpdate(ydoc);
      const base64 = encodeYjsStateToBase64(state);
      const { error: saveError } = await supabase
        .from('care_notes')
        .update({ yjs_state: base64 })
        .eq('id', careNoteId);

      if (saveError) throw saveError;

      // 2. Extract content for timeline
      const contentJson = editor.getJSON();
      const contentText = editor.getText();

      // 3. Create timeline entry (non-blocking - Yjs state is already saved)
      if (onCreateTimelineEntry && contentText.trim()) {
        try {
          await onCreateTimelineEntry(contentJson, contentText);
        } catch (timelineError) {
          console.error('[CareNoteEditor] Timeline entry failed (note still saved):', timelineError);
          toast.error('Note saved, but timeline entry failed. Check console for details.');
        }
      }

      // 4. Broadcast save event to other users
      if (provider?.awareness) {
        provider.awareness.setLocalStateField('saveEvent', {
          userId: currentUser.id,
          userName: currentUser.display_name,
          timestamp: new Date().toISOString(),
        });
        setTimeout(() => {
          provider.awareness!.setLocalStateField('saveEvent', null);
        }, 2000);
      }

      // 5. Update baseline and persist to localStorage
      const newBaseline = {
        text: contentText,
        json: contentJson,
        timestamp: new Date().toISOString(),
      };
      setBaselineContent(newBaseline);

      // Update localStorage with new baseline
      try {
        localStorage.setItem(`baseline_${careNoteId}`, JSON.stringify(newBaseline));
      } catch (error) {
        console.error('Failed to update baseline in localStorage:', error);
      }

      setSaveStatus('saved');
      toast.success('Care note saved and posted to timeline');
      setTimeout(() => setSaveStatus('idle'), 2000);
    } catch (error) {
      setSaveStatus('error');
      toast.error('Failed to save care note');
      console.error('Save error:', error);
    }
  }, [editor, ydoc, supabase, careNoteId, onCreateTimelineEntry, provider, currentUser]);

  const handleSelectiveSave = useCallback(async (selectedIndexes: number[]) => {
    if (!editor || !baselineContent || !diffData) return;

    setShowSaveDialog(false);
    setSaveStatus('saving');

    try {
      // Reconstruct content with only selected additions
      const selectedAdditions = diffData.additions.filter((_, idx) => selectedIndexes.includes(idx));
      const baseText = baselineContent.text;

      // Apply only selected changes to baseline
      let reconstructedText = baseText;
      selectedAdditions.forEach((change) => {
        reconstructedText += change.value;
      });

      // Set editor content to reconstructed text
      editor.commands.setContent({
        type: 'doc',
        content: [
          {
            type: 'paragraph',
            content: [{ type: 'text', text: reconstructedText }],
          },
        ],
      });

      // Wait for editor update
      await new Promise(resolve => setTimeout(resolve, 100));

      // Now proceed with normal save
      const state = Y.encodeStateAsUpdate(ydoc);
      const base64 = encodeYjsStateToBase64(state);
      const { error: saveError } = await supabase
        .from('care_notes')
        .update({ yjs_state: base64 })
        .eq('id', careNoteId);

      if (saveError) throw saveError;

      const contentJson = editor.getJSON();
      const contentText = editor.getText();

      if (onCreateTimelineEntry && contentText.trim()) {
        await onCreateTimelineEntry(contentJson, contentText);
      }

      if (provider?.awareness) {
        provider.awareness.setLocalStateField('saveEvent', {
          userId: currentUser.id,
          userName: currentUser.display_name,
          timestamp: new Date().toISOString(),
        });
        setTimeout(() => {
          provider.awareness!.setLocalStateField('saveEvent', null);
        }, 2000);
      }

      const newBaseline = {
        text: contentText,
        json: contentJson,
        timestamp: new Date().toISOString(),
      };
      setBaselineContent(newBaseline);
      localStorage.setItem(`baseline_${careNoteId}`, JSON.stringify(newBaseline));

      setSaveStatus('saved');
      toast.success(`Selective save: ${selectedIndexes.length} changes saved`);
      setTimeout(() => setSaveStatus('idle'), 2000);
    } catch (error) {
      setSaveStatus('error');
      toast.error('Failed to save selected changes');
      console.error('Selective save error:', error);
    }
  }, [editor, baselineContent, diffData, ydoc, supabase, careNoteId, onCreateTimelineEntry, provider, currentUser]);

  const handleCancelSave = useCallback(() => {
    setShowSaveDialog(false);
    setDiffData(null);
  }, []);

  const handleRevertToVersion = useCallback(async (versionId: string, content: string) => {
    if (!editor) return;

    // Set the editor content to the reverted version
    editor.commands.setContent({
      type: 'doc',
      content: [
        {
          type: 'paragraph',
          content: [{ type: 'text', text: content }],
        },
      ],
    });

    // Save the reverted content
    setSaveStatus('saving');
    try {
      const state = Y.encodeStateAsUpdate(ydoc);
      const base64 = encodeYjsStateToBase64(state);
      const { error: saveError } = await supabase
        .from('care_notes')
        .update({ yjs_state: base64 })
        .eq('id', careNoteId);

      if (saveError) throw saveError;

      // Create a new version entry for the revert
      await supabase.from('note_versions').insert({
        care_note_id: careNoteId,
        content_snapshot: { summary: content },
        changed_by: currentUser.id,
        change_summary: `Reverted to version ${versionId}`,
      });

      // Create timeline entry
      if (onCreateTimelineEntry) {
        await onCreateTimelineEntry(
          { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text: content }] }] },
          content
        );
      }

      // Update baseline
      const newBaseline = {
        text: content,
        json: editor.getJSON(),
        timestamp: new Date().toISOString(),
      };
      setBaselineContent(newBaseline);
      localStorage.setItem(`baseline_${careNoteId}`, JSON.stringify(newBaseline));

      setSaveStatus('saved');
      setTimeout(() => setSaveStatus('idle'), 2000);
    } catch (error) {
      console.error('Revert save failed:', error);
      setSaveStatus('error');
      throw error;
    }
  }, [editor, ydoc, supabase, careNoteId, currentUser, onCreateTimelineEntry]);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
      }
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        handleSave();
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleSave]);

  const statusConfig = {
    connected: { icon: Wifi, label: 'Live', variant: 'info' as const, dotColor: 'bg-primary' },
    connecting: { icon: Loader2, label: 'Connecting...', variant: 'medium' as const, dotColor: 'bg-amber-500' },
    disconnected: { icon: WifiOff, label: 'Offline', variant: 'critical' as const, dotColor: 'bg-red-500' },
    unavailable: { icon: WifiOff, label: 'Local Only', variant: 'medium' as const, dotColor: 'bg-amber-500' },
  };

  const statusInfo = statusConfig[status];
  const StatusIcon = statusInfo.icon;

  return (
    <Card className="flex flex-col overflow-hidden">
      <CardHeader className="pb-3 shrink-0 px-3 sm:px-6">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 sm:w-8 sm:h-8 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
              <FileEdit className="w-4 h-4 text-primary" />
            </div>
            <CardTitle className="text-sm sm:text-base">Care Note</CardTitle>
            <div className="flex items-center gap-1.5 ml-1">
              <span className={`w-1.5 h-1.5 rounded-full ${statusInfo.dotColor} ${status === 'connecting' ? 'animate-pulse' : ''}`} />
              <span className="text-[11px] text-muted-foreground">{statusInfo.label}</span>
            </div>
          </div>

          {/* Toolbar */}
          {!readOnly && editor && (
            <div className="flex items-center gap-0.5 bg-secondary/80 rounded-md p-0.5 flex-wrap">
              <Button
                size="sm"
                variant={editor.isActive('bold') ? 'default' : 'ghost'}
                className="h-7 w-7 p-0 rounded-md shrink-0"
                onClick={() => editor.chain().focus().toggleBold().run()}
              >
                <Bold className="w-3.5 h-3.5" />
              </Button>
              <Button
                size="sm"
                variant={editor.isActive('italic') ? 'default' : 'ghost'}
                className="h-7 w-7 p-0 rounded-md shrink-0"
                onClick={() => editor.chain().focus().toggleItalic().run()}
              >
                <Italic className="w-3.5 h-3.5" />
              </Button>
              <Button
                size="sm"
                variant={editor.isActive('bulletList') ? 'default' : 'ghost'}
                className="h-7 w-7 p-0 rounded-md shrink-0"
                onClick={() => editor.chain().focus().toggleBulletList().run()}
              >
                <List className="w-3.5 h-3.5" />
              </Button>
              <div className="w-px h-5 bg-border mx-0.5 shrink-0" />
              <Button
                size="sm"
                variant={editor.isActive('heading', { level: 2 }) ? 'default' : 'ghost'}
                className="h-7 w-7 p-0 rounded-md shrink-0"
                onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
              >
                <Heading2 className="w-3.5 h-3.5" />
              </Button>
              <Button
                size="sm"
                variant={editor.isActive('heading', { level: 3 }) ? 'default' : 'ghost'}
                className="h-7 w-7 p-0 rounded-md shrink-0"
                onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}
              >
                <Heading3 className="w-3.5 h-3.5" />
              </Button>
              <div className="w-px h-5 bg-border mx-0.5 shrink-0" />
              <Button
                size="sm"
                variant="ghost"
                className="h-7 px-2 rounded-md gap-1 shrink-0"
                onClick={() => setShowVersionHistory(true)}
                title="Version History"
              >
                <History className="w-3.5 h-3.5" />
                <span className="text-[11px] hidden sm:inline">History</span>
              </Button>
              <Button
                size="sm"
                variant={status === 'disconnected' || status === 'unavailable' ? 'default' : 'ghost'}
                className="h-7 px-2 rounded-md gap-1 shrink-0"
                onClick={handleSave}
                disabled={saveStatus === 'saving'}
              >
                {saveStatus === 'saving' ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : saveStatus === 'saved' ? (
                  <CheckCircle2 className="w-3.5 h-3.5" />
                ) : (
                  <Save className="w-3.5 h-3.5" />
                )}
                <span className="text-[11px] hidden sm:inline">Save</span>
              </Button>
            </div>
          )}
        </div>

        {/* Connected users */}
        {connectedUsers.length > 0 && (
          <div className="flex items-center gap-2 mt-2">
            <span className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">
              Editing:
            </span>
            {connectedUsers.map((user, idx) => (
              <div
                key={idx}
                className="flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-md"
                style={{ backgroundColor: `${user.color}10`, color: user.color }}
              >
                <span
                  className="w-1.5 h-1.5 rounded-full"
                  style={{ backgroundColor: user.color }}
                />
                <span className="font-medium">{user.name}</span>
                {user.section && (
                  <span className="text-[10px] opacity-60">in {user.section}</span>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Live diff preview toggle */}
        {!readOnly && editor && (
          <div className="flex items-center gap-3 mt-2 text-xs">
            <button
              onClick={() => setShowLivePreview(!showLivePreview)}
              className="flex items-center gap-1.5 text-muted-foreground hover:text-foreground transition"
            >
              <Eye className="w-3.5 h-3.5" />
              <span>Live Diff: {showLivePreview ? 'On' : 'Off'}</span>
            </button>

            {showLivePreview && liveDiff && (
              <div className="flex gap-3 px-2 py-1 bg-secondary rounded">
                <span className="text-green-600">+{liveDiff.additions}</span>
                <span className="text-red-600">-{liveDiff.deletions}</span>
              </div>
            )}
          </div>
        )}
      </CardHeader>

      {/* Info banner when collab server is unavailable */}
      {status === 'unavailable' && (
        <div className="mx-3 sm:mx-6 mb-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-800 flex items-center gap-2">
          <WifiOff className="w-3.5 h-3.5 shrink-0" />
          <span>Real-time collaboration unavailable. Your edits are local until you save.</span>
        </div>
      )}

      <CardContent className="pt-0 flex-1 overflow-auto px-3 sm:px-6">
        <div className="border border-border rounded-lg overflow-hidden min-h-[150px] sm:min-h-[200px]">
          <EditorContent editor={editor} />
        </div>
      </CardContent>

      {/* Save Confirmation Dialog */}
      {showSaveDialog && diffData && baselineContent && (
        <SaveConfirmDialog
          open={showSaveDialog}
          onOpenChange={setShowSaveDialog}
          onConfirm={handleConfirmSave}
          onConfirmSelective={handleSelectiveSave}
          onCancel={handleCancelSave}
          diffData={diffData}
          baselineTimestamp={baselineContent.timestamp}
          collaborators={connectedUsers}
        />
      )}

      {/* Version History Modal */}
      <VersionHistoryModal
        open={showVersionHistory}
        onOpenChange={setShowVersionHistory}
        careNoteId={careNoteId}
        currentContent={editor?.getText() || ''}
        onRevert={handleRevertToVersion}
      />
    </Card>
  );
}
