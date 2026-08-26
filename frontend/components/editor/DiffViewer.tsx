'use client';

import React from 'react';
import { diffLines, Change } from 'diff';

interface DiffViewerProps {
  oldText: string;
  newText: string;
  oldLabel?: string;
  newLabel?: string;
}

export function DiffViewer({ oldText, newText, oldLabel = 'Previous', newLabel = 'Current' }: DiffViewerProps) {
  const changes = diffLines(oldText, newText);

  const stats = {
    additions: changes.filter(c => c.added).reduce((sum, c) => sum + (c.count || 0), 0),
    deletions: changes.filter(c => c.removed).reduce((sum, c) => sum + (c.count || 0), 0),
  };

  return (
    <div className="space-y-3">
      {/* Stats */}
      <div className="flex items-center gap-4 text-xs">
        <span className="text-muted-foreground">Changes:</span>
        <span className="text-green-600 font-medium">+{stats.additions} additions</span>
        <span className="text-red-600 font-medium">-{stats.deletions} deletions</span>
      </div>

      {/* Diff display */}
      <div className="border border-border rounded-lg overflow-hidden">
        <div className="flex items-center justify-between bg-secondary/50 px-3 py-1.5 border-b border-border text-xs text-muted-foreground">
          <span>{oldLabel}</span>
          <span className="text-muted-foreground/60">→</span>
          <span>{newLabel}</span>
        </div>
        <div className="font-mono text-sm max-h-[400px] overflow-auto">
          {changes.map((change, index) => (
            <DiffLine key={index} change={change} />
          ))}
        </div>
      </div>
    </div>
  );
}

function DiffLine({ change }: { change: Change }) {
  if (change.added) {
    return (
      <div className="bg-green-50 border-l-4 border-green-500 px-3 py-1">
        {change.value.split('\n').filter(Boolean).map((line, i) => (
          <div key={i} className="text-green-800">
            <span className="select-none text-green-600 mr-2">+</span>
            {line}
          </div>
        ))}
      </div>
    );
  }

  if (change.removed) {
    return (
      <div className="bg-red-50 border-l-4 border-red-500 px-3 py-1">
        {change.value.split('\n').filter(Boolean).map((line, i) => (
          <div key={i} className="text-red-800 line-through">
            <span className="select-none text-red-600 mr-2">-</span>
            {line}
          </div>
        ))}
      </div>
    );
  }

  // Unchanged - show context
  const lines = change.value.split('\n').filter(Boolean);
  if (lines.length === 0) return null;

  // Show only first and last few lines if too many
  const maxContextLines = 3;
  if (lines.length > maxContextLines * 2) {
    return (
      <div className="px-3 py-1 text-muted-foreground">
        {lines.slice(0, maxContextLines).map((line, i) => (
          <div key={`start-${i}`}>
            <span className="select-none text-muted-foreground/50 mr-2">&nbsp;</span>
            {line}
          </div>
        ))}
        <div className="text-xs text-center py-1 text-muted-foreground/60">
          ... {lines.length - maxContextLines * 2} unchanged lines ...
        </div>
        {lines.slice(-maxContextLines).map((line, i) => (
          <div key={`end-${i}`}>
            <span className="select-none text-muted-foreground/50 mr-2">&nbsp;</span>
            {line}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="px-3 py-1 text-muted-foreground">
      {lines.map((line, i) => (
        <div key={i}>
          <span className="select-none text-muted-foreground/50 mr-2">&nbsp;</span>
          {line}
        </div>
      ))}
    </div>
  );
}

// Inline diff viewer for showing changes within a single line
export function InlineDiffViewer({ oldText, newText }: { oldText: string; newText: string }) {
  const changes = diffLines(oldText, newText);

  return (
    <div className="font-mono text-sm whitespace-pre-wrap">
      {changes.map((change, index) => {
        if (change.added) {
          return (
            <span key={index} className="bg-green-100 text-green-800">
              {change.value}
            </span>
          );
        }
        if (change.removed) {
          return (
            <span key={index} className="bg-red-100 text-red-800 line-through">
              {change.value}
            </span>
          );
        }
        return <span key={index}>{change.value}</span>;
      })}
    </div>
  );
}
