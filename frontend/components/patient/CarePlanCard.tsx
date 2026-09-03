'use client';

import { ChevronRight } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { partitionCarePlan } from '@/lib/care_plan';
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

/*
 * COLOUR: an incomplete care-plan item is NOT a clinical danger.
 *
 * These rows previously rendered `border-red-400 bg-red-50` — louder than the
 * critical-flags panel's own `border-red-200/60 bg-red-50/50`. A clinician
 * scanning for danger met fifteen red boxes meaning "not ticked yet" before
 * reaching one meaning "eGFR is falling". That is how red stops working.
 *
 * Red in this codebase is reserved for: critical flags, abnormal labs, the
 * maker-checker gate block, retraction notices, and the destructive button.
 * Incomplete is neutral; progress is primary.
 */
export function CarePlanCard({ items, score, onToggleItem }: CarePlanCardProps) {
  const safeScore = score || 0;

  // Indices are carried through the partition, never recomputed — see
  // lib/care_plan.ts for why that is load-bearing rather than a detail.
  const { open, done } = partitionCarePlan(items);

  return (
    <Card>
      <CardContent className="pt-4 pb-3">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold">Care Plan</h3>
          <Badge
            variant="secondary"
            className="text-xs bg-secondary text-muted-foreground"
          >
            {safeScore}%
          </Badge>
        </div>
        {/* Progress bar */}
        <div className="w-full bg-secondary rounded-full h-1.5 mb-3 overflow-hidden">
          <div
            className="h-1.5 rounded-full bg-primary transition-all duration-700"
            style={{ width: `${Math.min(safeScore, 100)}%` }}
          />
        </div>
        {/* Grouped open-first, completed folded away.
            THE INDEX IS CARRIED, NEVER RECOMPUTED. `entries` pairs each item
            with its position in the ORIGINAL array before any partition, so a
            click still toggles the row the clinician pointed at. Mapping over a
            filtered array here would silently persist the wrong item. */}
        <div className="space-y-1.5">
          {open.map(({ item, idx }) => (
            <Row key={idx} item={item} onToggle={onToggleItem && (() => onToggleItem(idx))} />
          ))}
          {items.length === 0 && (
            <p className="text-xs text-muted-foreground py-2">No care plan items yet.</p>
          )}
          {items.length > 0 && open.length === 0 && (
            <p className="text-xs text-muted-foreground py-2">All items complete.</p>
          )}
        </div>

        {done.length > 0 && (
          <details className="group mt-2 border-t border-border pt-2">
            {/* The count is on the summary because a closed section is an
                absence, and an absence must not read as "nothing here"
                (guardrails UI-1). A clinician can see that potassium
                monitoring was already done without opening it. */}
            <summary className="flex cursor-pointer list-none items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
              <ChevronRight aria-hidden className="h-3 w-3 shrink-0 transition-transform group-open:rotate-90" />
              {done.length} completed
            </summary>
            <div className="mt-1.5 space-y-1.5">
              {done.map(({ item, idx }) => (
                <Row key={idx} item={item} onToggle={onToggleItem && (() => onToggleItem(idx))} />
              ))}
            </div>
          </details>
        )}
      </CardContent>
    </Card>
  );
}

function Row({ item, onToggle }: { item: CarePlanItem; onToggle?: () => void }) {
  return (
    <div
      className="flex items-center gap-2 text-xs cursor-pointer hover:bg-secondary/50 rounded p-1.5 -mx-1"
      onClick={onToggle}
    >
      <div className={`w-3.5 h-3.5 rounded border-2 flex items-center justify-center shrink-0 ${
        item.completed ? 'bg-primary border-primary' : 'border-muted-foreground/40 bg-background'
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
  );
}
