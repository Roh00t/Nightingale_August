'use client';

import React from 'react';
import { Badge } from '@/components/ui/badge';
import { isSourceModified, type Highlight } from '@/lib/types';
import { AlertTriangle, FileWarning } from 'lucide-react';

interface CriticalFlagsProps {
  highlights: Highlight[];
  onFlagClick: (highlightId: string, sourceEntryId: string | null) => void;
  /**
   * Current `care_notes.version`, compared against each highlight's recorded
   * version to detect that the source text moved after extraction.
   */
  currentNoteVersion?: number | null;
}

export function CriticalFlags({ highlights, onFlagClick, currentNoteVersion }: CriticalFlagsProps) {
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
              {/* The source moved after this claim was extracted. Shown rather than
                                hidden: the silent version — following the link and reading
                                different text — leads a clinician to conclude the flag was
                                wrong, or that the new text supports it. */}
              {isSourceModified(flag, currentNoteVersion) && (
                              <p
                                className="mt-1.5 inline-flex items-center gap-1 rounded bg-orange-600 px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide text-white"
                                title={
                                  flag.source_note_version == null
                                    ? 'This highlight predates provenance tracking, so the source cannot be confirmed unchanged.'
                                    : `Extracted from version ${flag.source_note_version}; the note is now at ${currentNoteVersion}.`
                                }
                              >
                                <FileWarning className="w-3 h-3" />
                                [SOURCE EDITED &mdash; VERIFY NOTE]
                              </p>
                            )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
