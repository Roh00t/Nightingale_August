'use client';

import React from 'react';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { getTrustBadgeIcon, getConfidenceLabel } from '@/lib/utils';
import type { TrustBadge as TrustBadgeType } from '@/lib/types';
import { ShieldCheck, Sparkles, Heart, ClipboardList, AlertTriangle, type LucideIcon } from 'lucide-react';

const iconMap: Record<string, LucideIcon> = {
  ShieldCheck,
  Sparkles,
  Heart,
  ClipboardList,
  AlertTriangle,
};

interface TrustBadgeProps {
  badge: TrustBadgeType;
  size?: 'sm' | 'md';
  showLabel?: boolean;
}

const variantMap: Record<TrustBadgeType['type'], 'clinician' | 'ai' | 'patient' | 'staff' | 'conflict'> = {
  clinician_verified: 'clinician',
  ai_generated: 'ai',
  patient_reported: 'patient',
  staff_noted: 'staff',
  conflict: 'conflict',
};

export function TrustBadge({ badge, size = 'sm', showLabel = true }: TrustBadgeProps) {
  const iconName = getTrustBadgeIcon(badge.type);
  const IconComponent = iconMap[iconName];
  const variant = variantMap[badge.type];

  const confidenceInfo = badge.confidence !== undefined
    ? getConfidenceLabel(badge.confidence)
    : null;

  const iconSize = size === 'sm' ? 'w-3 h-3' : 'w-3.5 h-3.5';

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          variant={variant}
          className={size === 'sm' ? 'text-[10px] px-1.5 py-0' : 'text-xs px-2 py-0.5'}
        >
          {IconComponent && <IconComponent className={`${iconSize} mr-0.5 shrink-0`} />}
          {showLabel && <span>{badge.label}</span>}
          {confidenceInfo && (
            <span className={`ml-1 ${confidenceInfo.color}`}>
              {Math.round(badge.confidence! * 100)}%
            </span>
          )}
        </Badge>
      </TooltipTrigger>
      <TooltipContent>
        <div className="text-xs">
          <p className="font-medium">{badge.label}</p>
          {confidenceInfo && (
            <p>Confidence: {confidenceInfo.label} ({Math.round(badge.confidence! * 100)}%)</p>
          )}
          {badge.type === 'ai_generated' && (
            <p className="text-muted-foreground mt-1">Click for AI reasoning chain</p>
          )}
        </div>
      </TooltipContent>
    </Tooltip>
  );
}
