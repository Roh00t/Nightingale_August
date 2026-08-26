'use client';

import React from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import type { Highlight } from '@/lib/types';
import { UserPlus, CheckCircle2, Clock } from 'lucide-react';

interface ActionItemsProps {
  highlights: Highlight[];
  onAssign: (highlightId: string) => void;
  onDone: (highlightId: string) => void;
  onDefer: (highlightId: string) => void;
}

export function ActionItems({ highlights, onAssign, onDone, onDefer }: ActionItemsProps) {
  const actionItems = highlights.filter(
    (h) => h.risk_level === 'high' || h.risk_level === 'critical'
  ).filter((h) => h.is_accepted !== false);

  if (actionItems.length === 0) return null;

  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
        Open Actions ({actionItems.length})
      </h4>
      {actionItems.map((item) => (
        <div
          key={item.id}
          className="flex items-center justify-between gap-2 p-3 rounded-lg border border-border bg-card"
        >
          <div className="flex items-center gap-2.5 min-w-0">
            <Badge variant={item.risk_level as 'critical' | 'high'} className="text-[10px] shrink-0">
              {item.risk_level.toUpperCase()}
            </Badge>
            <span className="text-sm truncate">{item.content_snippet}</span>
          </div>
          <div className="flex gap-1 shrink-0">
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-[10px] px-2 gap-1"
              onClick={() => onAssign(item.id)}
            >
              <UserPlus className="w-3 h-3" />
              Assign
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-[10px] px-2 gap-1 text-green-600 hover:bg-green-50"
              onClick={() => onDone(item.id)}
            >
              <CheckCircle2 className="w-3 h-3" />
              Done
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-[10px] px-2 gap-1"
              onClick={() => onDefer(item.id)}
            >
              <Clock className="w-3 h-3" />
              Defer
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}
