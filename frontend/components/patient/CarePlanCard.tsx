'use client';

import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import type { CarePlanItem } from '@/lib/types';

interface CarePlanCardProps {
  /**
   * Rendered in the order given, and NEVER re-sorted or filtered.
   *
   * `onToggleItem` carries the index into THIS array, and the parent writes
   * `glance_cache.care_plan_items[index]` straight back to Postgres
   * (PatientWorkspace.tsx, handleToggleCarePlanItem). Reordering or filtering
   * for display would toggle — and persist — a different item than the one the
   * clinician clicked, with no error and no way to notice.
   *
   * If you want grouping, group in the parent and pass stable indices, or move
   * the callback to a stable id first.
   */
  items: CarePlanItem[];
  score: number;
  /** Omit to render read-only. */
  onToggleItem?: (index: number) => void;
}

export function CarePlanCard({ items, score, onToggleItem }: CarePlanCardProps) {
  const safeScore = score || 0;

  return (
    <Card>
      <CardContent className="pt-4 pb-3">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold">Care Plan</h3>
          <Badge
            variant="secondary"
            className={`text-xs ${safeScore >= 50 ? 'bg-primary/10 text-primary' : 'bg-red-50 text-red-600'}`}
          >
            {safeScore}%
          </Badge>
        </div>
        {/* Progress bar */}
        <div className="w-full bg-secondary rounded-full h-1.5 mb-3 overflow-hidden">
          <div
            className={`h-1.5 rounded-full transition-all duration-700 ${
              safeScore >= 50 ? 'bg-primary' : 'bg-red-500'
            }`}
            style={{ width: `${Math.min(safeScore, 100)}%` }}
          />
        </div>
        <div className="space-y-1.5">
          {items.map((item, idx) => (
            <div
              key={idx}
              className="flex items-center gap-2 text-xs cursor-pointer hover:bg-secondary/50 rounded p-1.5 -mx-1"
              onClick={() => onToggleItem?.(idx)}
            >
              <div className={`w-3.5 h-3.5 rounded border-2 flex items-center justify-center shrink-0 ${
                item.completed ? 'bg-primary border-primary' : 'border-red-400 bg-red-50'
              }`}>
                {item.completed && (
                  <svg className="w-2.5 h-2.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                  </svg>
                )}
              </div>
              <span className={item.completed ? 'line-through text-muted-foreground' : ''}>
                {item.label}
              </span>
            </div>
          ))}
          {items.length === 0 && (
            <p className="text-xs text-muted-foreground py-2">No care plan items yet.</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
