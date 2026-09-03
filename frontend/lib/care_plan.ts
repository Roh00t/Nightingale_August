import type { CarePlanItem } from '@/lib/types';

/** An item paired with its position in the ORIGINAL, unpartitioned array. */
export interface IndexedCarePlanItem {
  item: CarePlanItem;
  idx: number;
}

/**
 * Split a care plan into open and completed, carrying original indices.
 *
 * This exists as a tested function rather than three inline lines because of
 * what the index means. `handleToggleCarePlanItem(index)` in PatientWorkspace
 * indexes `glance_cache.care_plan_items` and writes the result straight back to
 * Postgres. If a display regrouping renumbers the rows, clicking "Schedule
 * cardiology appointment" ticks something else — persisted, with no error, and
 * no way for the clinician to notice beyond re-reading the whole list.
 *
 * So the index is carried through the partition, never recomputed from the
 * filtered array. That is the entire contract, and it is what the tests check.
 */
export function partitionCarePlan(items: CarePlanItem[]): {
  open: IndexedCarePlanItem[];
  done: IndexedCarePlanItem[];
} {
  const entries = items.map((item, idx) => ({ item, idx }));
  return {
    open: entries.filter((e) => !e.item.completed),
    done: entries.filter((e) => e.item.completed),
  };
}
