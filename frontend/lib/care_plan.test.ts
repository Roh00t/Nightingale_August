import { describe, it, expect } from 'vitest';
import { partitionCarePlan } from './care_plan';
import type { CarePlanItem } from '@/lib/types';

const item = (label: string, completed = false): CarePlanItem => ({ label, completed });

describe('partitionCarePlan', () => {
  it('separates open from completed', () => {
    const { open, done } = partitionCarePlan([item('a'), item('b', true), item('c')]);
    expect(open.map((e) => e.item.label)).toEqual(['a', 'c']);
    expect(done.map((e) => e.item.label)).toEqual(['b']);
  });

  it('carries ORIGINAL indices, not positions in the filtered array', () => {
    // The whole reason this function exists. `c` is at index 2 in the source
    // array and index 1 in `open` — the parent writes by source index, so 2 is
    // the only correct answer. Returning 1 would tick the wrong item and
    // persist it.
    const { open, done } = partitionCarePlan([item('a'), item('b', true), item('c')]);
    expect(open.map((e) => e.idx)).toEqual([0, 2]);
    expect(done.map((e) => e.idx)).toEqual([1]);
  });

  it('every returned index resolves back to its own item', () => {
    const items = [item('a', true), item('b'), item('c', true), item('d')];
    const { open, done } = partitionCarePlan(items);
    for (const e of [...open, ...done]) {
      expect(items[e.idx]).toBe(e.item);
    }
  });

  it('handles an empty plan', () => {
    expect(partitionCarePlan([])).toEqual({ open: [], done: [] });
  });

  it('handles an all-complete plan', () => {
    const { open, done } = partitionCarePlan([item('a', true), item('b', true)]);
    expect(open).toEqual([]);
    expect(done.map((e) => e.idx)).toEqual([0, 1]);
  });

  it('does not mutate or reorder the source', () => {
    const items = [item('a'), item('b', true)];
    const copy = [...items];
    partitionCarePlan(items);
    expect(items).toEqual(copy);
  });
});
