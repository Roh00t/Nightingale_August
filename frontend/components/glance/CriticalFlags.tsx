'use client';

import React from 'react';
import { Badge } from '@/components/ui/badge';
import type { Highlight } from '@/lib/types';
import { AlertTriangle } from 'lucide-react';

interface CriticalFlagsProps {
  highlights: Highlight[];
  onFlagClick: (highlightId: string, sourceEntryId: string | null) => void;
}

export function CriticalFlags({ highlights, onFlagClick }: CriticalFlagsProps) {
  const criticalFlags = highlights.filter((h) => h.risk_level === 'critical' && h.is_accepted !== false);

  if (criticalFlags.length === 0) return null;

  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold text-red-600 uppercase tracking-wider flex items-center gap-1.5">
        <AlertTriangle className="w-3.5 h-3.5" />
        Critical Flags ({criticalFlags.length})
      </h4>
      {criticalFlags.map((flag) => (
        <div
          key={flag.id}
          className="p-3 rounded-lg border border-red-200/60 bg-red-50/50 cursor-pointer hover:bg-red-50 transition-colors"
          onClick={() => onFlagClick(flag.id, flag.source_entry_id)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              onFlagClick(flag.id, flag.source_entry_id);
            }
          }}
        >
          <div className="flex items-start gap-2.5">
            <Badge variant="critical" className="text-[10px] shrink-0 mt-0.5">
              CRITICAL
            </Badge>
            <div>
              <p className="text-sm font-medium text-red-800">{flag.content_snippet}</p>
              <p className="text-xs text-red-600/80 mt-0.5">{flag.risk_reason}</p>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
