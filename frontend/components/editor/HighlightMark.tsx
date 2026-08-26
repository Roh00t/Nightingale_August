'use client';

import React from 'react';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import type { Highlight, RiskLevel } from '@/lib/types';
import { getConfidenceLabel } from '@/lib/utils';

interface HighlightMarkProps {
  highlight: Highlight;
  children: React.ReactNode;
  onClick: (highlightId: string) => void;
  onAccept: (highlightId: string) => void;
  onReject: (highlightId: string) => void;
}

export function HighlightMark({
  highlight,
  children,
  onClick,
  onAccept,
  onReject,
}: HighlightMarkProps) {
  const confidence = getConfidenceLabel(highlight.importance_score);
  const [expanded, setExpanded] = React.useState(false);

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className="highlight-mark cursor-pointer relative inline"
          data-risk={highlight.risk_level}
          onClick={() => {
            if (expanded) {
              onClick(highlight.id);
            } else {
              setExpanded(true);
            }
          }}
        >
          {children}

          {/* Layer 1: Always visible - small confidence indicator */}
          <sup className={`text-[9px] font-bold ml-0.5 ${confidence.color}`}>
            {confidence.label[0]}
          </sup>
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs">
        {/* Layer 2: On hover - provenance + reasoning */}
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Badge
              variant={highlight.risk_level as 'critical' | 'high' | 'medium' | 'low' | 'info'}
              className="text-[10px]"
            >
              {highlight.risk_level.toUpperCase()}
            </Badge>
            <span className={`text-xs font-medium ${confidence.color}`}>
              {confidence.label} confidence ({Math.round(highlight.importance_score * 100)}%)
            </span>
          </div>
          <p className="text-xs font-medium">{highlight.risk_reason}</p>
          <p className="text-[10px] text-muted-foreground">
            Click to navigate to source • Y to accept • N to reject
          </p>
        </div>
      </TooltipContent>
    </Tooltip>
  );
}
