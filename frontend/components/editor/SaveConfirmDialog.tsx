'use client';

import React, { useState } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Clock, Users, AlertCircle, AlertTriangle } from 'lucide-react';
import { getRelativeTime, cn } from '@/lib/utils';

interface DiffChange {
  value: string;
  added?: boolean;
  removed?: boolean;
  count?: number;
}

interface SaveConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  onCancel: () => void;
  onConfirmSelective?: (selectedChangeIndexes: number[]) => void;
  diffData: {
    additions: DiffChange[];
    deletions: DiffChange[];
    unchanged: DiffChange[];
  };
  baselineTimestamp: string;
  collaborators: Array<{ name: string; role: string; color: string }>;
}

export function SaveConfirmDialog({
  open,
  onOpenChange,
  onConfirm,
  onCancel,
  onConfirmSelective,
  diffData,
  baselineTimestamp,
  collaborators,
}: SaveConfirmDialogProps) {
  const [selectedChanges, setSelectedChanges] = useState<Set<number>>(new Set());
  const [resolutionMode, setResolutionMode] = useState<'merge' | 'selective'>('merge');

  const baselineAge = Date.now() - new Date(baselineTimestamp).getTime();
  const THIRTY_MINUTES = 30 * 60 * 1000;
  const isOldBaseline = baselineAge > THIRTY_MINUTES;

  const additionCount = diffData.additions.reduce((sum, c) => sum + (c.count || 0), 0);
  const deletionCount = diffData.deletions.reduce((sum, c) => sum + (c.count || 0), 0);

  const handleSelectAll = () => {
    const allIndexes = new Set(diffData.additions.map((_, idx) => idx));
    setSelectedChanges(allIndexes);
  };

  const handleDeselectAll = () => {
    setSelectedChanges(new Set());
  };

  const toggleChange = (idx: number) => {
    const newSet = new Set(selectedChanges);
    if (newSet.has(idx)) {
      newSet.delete(idx);
    } else {
      newSet.add(idx);
    }
    setSelectedChanges(newSet);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>Review Changes Before Saving</DialogTitle>
          <DialogDescription className="flex items-center gap-2 mt-2">
            <Clock className="w-4 h-4" />
            <span>Changes since {getRelativeTime(baselineTimestamp)}</span>
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 space-y-4 overflow-hidden">
          {isOldBaseline && (
            <div className="p-3 bg-amber-50 border border-amber-300 rounded-lg flex items-start gap-2">
              <AlertCircle className="h-4 w-4 text-amber-600 mt-0.5" />
              <p className="text-sm text-amber-900">
                Your baseline is older than 30 minutes. Consider refreshing to see the latest changes.
              </p>
            </div>
          )}

          {collaborators.length > 0 && (
            <div className="p-3 bg-blue-50 border border-blue-200 rounded-lg">
              <div className="flex items-center gap-2 mb-2">
                <Users className="w-4 h-4 text-blue-600" />
                <p className="text-sm font-medium text-blue-900">
                  {collaborators.length} other user(s) currently editing
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                {collaborators.map((user) => (
                  <Badge
                    key={user.name}
                    className="text-xs"
                    style={{ backgroundColor: `${user.color}20`, color: user.color, borderColor: user.color }}
                  >
                    {user.name}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          {/* Resolution mode selector */}
          <div className="flex gap-2 p-2 bg-secondary rounded-lg">
            <button
              onClick={() => setResolutionMode('merge')}
              className={cn(
                'flex-1 px-3 py-2 rounded text-sm font-medium transition',
                resolutionMode === 'merge'
                  ? 'bg-primary text-primary-foreground'
                  : 'hover:bg-secondary-hover'
              )}
            >
              Save All Changes (Merged)
            </button>
            <button
              onClick={() => setResolutionMode('selective')}
              className={cn(
                'flex-1 px-3 py-2 rounded text-sm font-medium transition',
                resolutionMode === 'selective'
                  ? 'bg-primary text-primary-foreground'
                  : 'hover:bg-secondary-hover'
              )}
            >
              Select Changes to Keep
            </button>
          </div>

          <div className="flex gap-6 p-3 bg-secondary rounded-lg">
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full bg-green-500" />
              <span className="text-sm font-medium">{additionCount} addition{additionCount !== 1 ? 's' : ''}</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full bg-red-500" />
              <span className="text-sm font-medium">{deletionCount} deletion{deletionCount !== 1 ? 's' : ''}</span>
            </div>
          </div>

          {/* Select All / Deselect All in selective mode */}
          {resolutionMode === 'selective' && (
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="ghost"
                onClick={handleSelectAll}
              >
                Select All Additions
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={handleDeselectAll}
              >
                Deselect All
              </Button>
            </div>
          )}

          {/* Warning for selective mode */}
          {resolutionMode === 'selective' && selectedChanges.size > 0 && (
            <div className="p-3 bg-amber-50 border border-amber-300 rounded-lg flex items-start gap-2">
              <AlertTriangle className="h-4 w-4 text-amber-600 mt-0.5" />
              <div className="text-sm text-amber-900">
                <p className="font-medium">Selective save will discard unselected changes</p>
                <p className="text-xs mt-1">
                  Unselected additions will be removed from the document permanently.
                </p>
              </div>
            </div>
          )}

          <div className="flex-1 border rounded-lg overflow-hidden">
            <ScrollArea className="h-[400px]">
              <div className="font-mono text-xs p-4 space-y-0.5">
                {diffData.deletions.map((change, idx) => (
                  <div key={`del-${idx}`} className="bg-red-50 text-red-900 px-3 py-1.5 rounded">
                    <span className="text-red-500 font-bold mr-2">-</span>
                    {change.value}
                  </div>
                ))}
                {diffData.additions.map((change, idx) => (
                  <div
                    key={`add-${idx}`}
                    className={cn(
                      'bg-green-50 text-green-900 px-3 py-1.5 rounded flex items-start gap-2',
                      resolutionMode === 'selective' && 'cursor-pointer hover:bg-green-100'
                    )}
                    onClick={() => {
                      if (resolutionMode === 'selective') {
                        toggleChange(idx);
                      }
                    }}
                  >
                    {resolutionMode === 'selective' && (
                      <input
                        type="checkbox"
                        checked={selectedChanges.has(idx)}
                        onChange={() => {}} // Handled by div onClick
                        className="mt-1"
                      />
                    )}
                    <div className="flex-1">
                      <span className="text-green-500 font-bold mr-2">+</span>
                      {change.value}
                    </div>
                  </div>
                ))}
                {diffData.unchanged.slice(0, 5).map((change, idx) => (
                  <div key={`unc-${idx}`} className="text-muted-foreground px-3 py-1.5">
                    {change.value}
                  </div>
                ))}
              </div>
            </ScrollArea>
          </div>

          <p className="text-xs text-muted-foreground">
            Saving will merge all concurrent edits and create a new timeline entry.
          </p>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>Cancel</Button>
          {resolutionMode === 'merge' ? (
            <Button onClick={onConfirm}>Save Merged Version</Button>
          ) : (
            <Button
              onClick={() => onConfirmSelective?.(Array.from(selectedChanges))}
              disabled={selectedChanges.size === 0}
            >
              Save Selected Changes ({selectedChanges.size})
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
