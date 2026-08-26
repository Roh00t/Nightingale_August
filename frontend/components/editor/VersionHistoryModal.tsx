'use client';

import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { DiffViewer } from './DiffViewer';
import { createClient } from '@/lib/supabase/client';
import { getRelativeTime } from '@/lib/utils';
import type { NoteVersion, Profile } from '@/lib/types';
import { History, RotateCcw, ChevronRight, Loader2, GitCompare, Check } from 'lucide-react';
import { toast } from 'sonner';

interface VersionHistoryModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  careNoteId: string;
  currentContent: string;
  onRevert: (versionId: string, content: string) => Promise<void>;
}

interface VersionWithAuthor extends NoteVersion {
  author?: Profile;
}

export function VersionHistoryModal({
  open,
  onOpenChange,
  careNoteId,
  currentContent,
  onRevert,
}: VersionHistoryModalProps) {
  const supabase = createClient();
  const [versions, setVersions] = useState<VersionWithAuthor[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedVersion, setSelectedVersion] = useState<VersionWithAuthor | null>(null);
  const [compareVersion, setCompareVersion] = useState<VersionWithAuthor | null>(null);
  const [isComparing, setIsComparing] = useState(false);
  const [reverting, setReverting] = useState(false);

  useEffect(() => {
    if (!open) return;

    async function loadVersions() {
      setLoading(true);

      // First get versions
      const { data: versionsData, error: versionsError } = await supabase
        .from('note_versions')
        .select('*')
        .eq('care_note_id', careNoteId)
        .order('version_number', { ascending: false });

      if (versionsError) {
        console.error('Failed to load versions:', versionsError);
        toast.error('Failed to load version history');
        setLoading(false);
        return;
      }

      // Then get profiles for the authors
      const authorIds = [...new Set(versionsData.map(v => v.changed_by).filter(Boolean))];
      let profilesMap: Record<string, Profile> = {};

      if (authorIds.length > 0) {
        const { data: profilesData } = await supabase
          .from('profiles')
          .select('*')
          .in('id', authorIds);

        if (profilesData) {
          profilesMap = Object.fromEntries(profilesData.map(p => [p.id, p]));
        }
      }

      // Merge versions with author profiles
      const versionsWithAuthors = versionsData.map(v => ({
        ...v,
        author: v.changed_by ? profilesMap[v.changed_by] : undefined,
      }));

      setVersions(versionsWithAuthors as VersionWithAuthor[]);
      if (versionsWithAuthors.length > 0) {
        setSelectedVersion(versionsWithAuthors[0] as VersionWithAuthor);
      }
      setLoading(false);
    }

    loadVersions();
  }, [open, careNoteId, supabase]);

  const handleRevert = async () => {
    if (!selectedVersion || !selectedVersion.content_snapshot) return;

    setReverting(true);
    try {
      // Extract text content from snapshot
      const snapshot = selectedVersion.content_snapshot as { summary?: string };
      const contentText = snapshot.summary || JSON.stringify(snapshot);

      await onRevert(selectedVersion.id, contentText);
      toast.success(`Reverted to version ${selectedVersion.version_number}`);
      onOpenChange(false);
    } catch (error) {
      console.error('Revert failed:', error);
      toast.error('Failed to revert to this version');
    } finally {
      setReverting(false);
    }
  };

  const getVersionContent = (version: VersionWithAuthor): string => {
    if (!version.content_snapshot) return '';
    const snapshot = version.content_snapshot as { summary?: string };
    return snapshot.summary || JSON.stringify(snapshot, null, 2);
  };

  const toggleCompare = (version: VersionWithAuthor) => {
    if (compareVersion?.id === version.id) {
      setCompareVersion(null);
      setIsComparing(false);
    } else if (selectedVersion && selectedVersion.id !== version.id) {
      setCompareVersion(version);
      setIsComparing(true);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <History className="w-5 h-5" />
            Version History
          </DialogTitle>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        ) : versions.length === 0 ? (
          <div className="text-center py-12 text-muted-foreground">
            <History className="w-10 h-10 mx-auto mb-3 opacity-20" />
            <p>No version history available</p>
          </div>
        ) : (
          <div className="flex gap-4 flex-1 overflow-hidden">
            {/* Version list */}
            <ScrollArea className="w-72 shrink-0 border-r border-border pr-4">
              <div className="space-y-2">
                {versions.map((version) => {
                  const isSelected = selectedVersion?.id === version.id;
                  const isCompareTarget = compareVersion?.id === version.id;

                  return (
                    <div
                      key={version.id}
                      className={`p-3 rounded-lg border cursor-pointer transition-all ${
                        isSelected
                          ? 'bg-primary/5 border-primary/30'
                          : isCompareTarget
                          ? 'bg-blue-50 border-blue-200'
                          : 'border-border hover:border-primary/20 hover:bg-secondary/50'
                      }`}
                      onClick={() => setSelectedVersion(version)}
                    >
                      <div className="flex items-center justify-between mb-1">
                        <Badge variant="outline" className="text-xs">
                          v{version.version_number}
                        </Badge>
                        <span className="text-xs text-muted-foreground">
                          {getRelativeTime(version.created_at)}
                        </span>
                      </div>
                      <p className="text-sm font-medium line-clamp-2">
                        {version.change_summary || 'No description'}
                      </p>
                      <p className="text-xs text-muted-foreground mt-1">
                        by {version.author?.display_name || 'Unknown'}
                      </p>

                      {/* Compare checkbox */}
                      {selectedVersion && selectedVersion.id !== version.id && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            toggleCompare(version);
                          }}
                          className={`mt-2 flex items-center gap-1.5 text-xs ${
                            isCompareTarget ? 'text-blue-600' : 'text-muted-foreground hover:text-foreground'
                          }`}
                        >
                          <div className={`w-3.5 h-3.5 rounded border flex items-center justify-center ${
                            isCompareTarget ? 'bg-blue-600 border-blue-600' : 'border-border'
                          }`}>
                            {isCompareTarget && <Check className="w-2.5 h-2.5 text-white" />}
                          </div>
                          Compare
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </ScrollArea>

            {/* Version details / diff view */}
            <div className="flex-1 flex flex-col overflow-hidden">
              {selectedVersion && (
                <>
                  <div className="flex items-center justify-between mb-4">
                    <div>
                      <h3 className="font-semibold flex items-center gap-2">
                        Version {selectedVersion.version_number}
                        {isComparing && compareVersion && (
                          <>
                            <ChevronRight className="w-4 h-4 text-muted-foreground" />
                            <span className="text-blue-600">vs v{compareVersion.version_number}</span>
                          </>
                        )}
                      </h3>
                      <p className="text-sm text-muted-foreground">
                        {new Date(selectedVersion.created_at).toLocaleString()}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      {isComparing && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            setCompareVersion(null);
                            setIsComparing(false);
                          }}
                        >
                          <GitCompare className="w-4 h-4 mr-1" />
                          Exit Compare
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant="default"
                        onClick={handleRevert}
                        disabled={reverting || selectedVersion.version_number === versions[0]?.version_number}
                      >
                        {reverting ? (
                          <Loader2 className="w-4 h-4 mr-1 animate-spin" />
                        ) : (
                          <RotateCcw className="w-4 h-4 mr-1" />
                        )}
                        Revert to This Version
                      </Button>
                    </div>
                  </div>

                  <ScrollArea className="flex-1">
                    {isComparing && compareVersion ? (
                      <DiffViewer
                        oldText={getVersionContent(compareVersion)}
                        newText={getVersionContent(selectedVersion)}
                        oldLabel={`v${compareVersion.version_number}`}
                        newLabel={`v${selectedVersion.version_number}`}
                      />
                    ) : (
                      <div className="space-y-4">
                        <div>
                          <h4 className="text-xs font-medium text-muted-foreground uppercase mb-2">
                            Change Summary
                          </h4>
                          <p className="text-sm">
                            {selectedVersion.change_summary || 'No description provided'}
                          </p>
                        </div>
                        <div>
                          <h4 className="text-xs font-medium text-muted-foreground uppercase mb-2">
                            Content Snapshot
                          </h4>
                          <div className="p-4 bg-secondary/30 rounded-lg border border-border text-sm font-mono whitespace-pre-wrap">
                            {getVersionContent(selectedVersion) || 'No content available'}
                          </div>
                        </div>

                        {/* Diff against current */}
                        {currentContent && (
                          <div>
                            <h4 className="text-xs font-medium text-muted-foreground uppercase mb-2">
                              Changes from Current
                            </h4>
                            <DiffViewer
                              oldText={getVersionContent(selectedVersion)}
                              newText={currentContent}
                              oldLabel={`v${selectedVersion.version_number}`}
                              newLabel="Current"
                            />
                          </div>
                        )}
                      </div>
                    )}
                  </ScrollArea>
                </>
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
