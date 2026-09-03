'use client';

import { AlertCircle, Clock, Plus, TrendingDown, TrendingUp } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { ChangeSinceLastVisit } from '@/lib/types';

const changeIcons: Record<string, React.ElementType> = {
  new: Plus,
  improved: TrendingUp,
  concerning: TrendingDown,
  unresolved: Clock,
};

const changeColors: Record<string, string> = {
  new: 'text-neutral-600 bg-neutral-50',
  improved: 'text-green-600 bg-green-50',
  concerning: 'text-red-600 bg-red-50',
  unresolved: 'text-neutral-600 bg-neutral-50',
};

interface ChangesSinceLastVisitCardProps {
  changes: ChangeSinceLastVisit[];
  /**
   * Render bare rows without the surrounding Card — for when the caller has
   * already provided a titled container (e.g. CollapsibleSection).
   */
  bare?: boolean;
}

/**
 * Lifted out of TopCard so it can be collapsed independently of "At a Glance".
 *
 * Renders nothing when there are no changes: the caller decides whether an
 * empty state needs stating. In the clinician column that decision is made by
 * the section's subtitle count, so an empty card would be duplicate furniture.
 */
export function ChangesSinceLastVisitCard({ changes, bare = false }: ChangesSinceLastVisitCardProps) {
  if (changes.length === 0) return null;

  const rows = (
    <>
      {changes.map((change, idx) => {
        const Icon = changeIcons[change.type] || AlertCircle;
        const colorClass = changeColors[change.type] || 'text-gray-600 bg-gray-50';
        const [textColor, bgColor] = colorClass.split(' ');

        return (
          <div key={idx} className="flex items-start gap-3 py-1.5">
            <div className={`w-6 h-6 rounded-lg ${bgColor} flex items-center justify-center shrink-0 mt-0.5`}>
              <Icon className={`w-3.5 h-3.5 ${textColor}`} />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm leading-snug break-words">{change.text}</p>
              <Badge variant="outline" className="text-[11px] mt-1 px-1.5 break-words">
                {change.detail}
              </Badge>
            </div>
          </div>
        );
      })}
    </>
  );

  if (bare) return <div className="space-y-2">{rows}</div>;

  return (
    <Card>
      <CardHeader className="pb-2 px-3 sm:px-6">
        <CardTitle className="text-sm">Changes Since Last Visit</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 px-3 sm:px-6">{rows}</CardContent>
    </Card>
  );
}
