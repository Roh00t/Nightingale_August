'use client';

import { useRef } from 'react';
import { ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';
import { emit, type TelemetryComponent } from '@/lib/telemetry';

interface CollapsibleSectionProps {
  /** Always names the contents in words, e.g. "At a Glance". */
  title: string;
  /**
   * A count or status appended to the summary line, e.g. "3 findings · 1 critical".
   *
   * Effectively required whenever the hidden content is non-empty. A closed
   * section is an absence, and `guardrails.md` UI-1 forbids letting an absence
   * carry meaning — a clinician must be able to tell "closed, contains 3 things"
   * from "closed, contains nothing" without opening it.
   */
  subtitle?: string;
  /**
   * Renders a plain <section> with NO disclosure control at all — deliberately
   * not `<details open>`.
   *
   * Two reasons. Clinically: a critical finding must not be collapsible even
   * deliberately, and `<details open>` leaves the triangle there to click.
   * Technically: `<details open={x}>` is not a controlled input, so a user
   * toggle desyncs the DOM from the prop with no re-render, and forcing it back
   * open properly would need state this component is not allowed to have.
   */
  forceOpen?: boolean;
  /**
   * Fixed id from the telemetry union — never derived from `title`, a prop or
   * element text, which is the path by which a drug name reaches an analytics
   * column. Omit to record nothing.
   */
  telemetryId?: TelemetryComponent;
  children: React.ReactNode;
  className?: string;
}

export function CollapsibleSection({
  title,
  subtitle,
  forceOpen = false,
  telemetryId,
  children,
  className,
}: CollapsibleSectionProps) {
  // Time from mount to first expand — the measure of whether default-closed is
  // helping or taxing. If this is consistently under a few seconds, the section
  // should not have been closed and the data says so rather than the argument.
  const mountedAt = useRef(Date.now());
  const openedAt = useRef<number | null>(null);

  const handleToggle = (e: React.SyntheticEvent<HTMLDetailsElement>) => {
    if (!telemetryId) return;
    const isOpen = e.currentTarget.open;
    if (isOpen) {
      openedAt.current = Date.now();
      emit(telemetryId, 'expand', { dwellMs: Date.now() - mountedAt.current });
    } else {
      // Dwell on collapse is the "orphaned expansion" signal: opened, glanced
      // at for under a second, closed again means they were hunting for
      // something that belongs higher up.
      const dwell = openedAt.current ? Date.now() - openedAt.current : undefined;
      openedAt.current = null;
      emit(telemetryId, 'collapse', { dwellMs: dwell });
    }
  };
  const heading = (
    <>
      <span className="text-sm font-semibold">{title}</span>
      {subtitle && (
        <span className="text-xs font-normal text-muted-foreground">{subtitle}</span>
      )}
    </>
  );

  if (forceOpen) {
    return (
      <section className={cn('rounded-lg border border-border bg-card', className)}>
        <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
          {heading}
        </div>
        <div className="p-3">{children}</div>
      </section>
    );
  }

  return (
    <details
      className={cn('group rounded-lg border border-border bg-card', className)}
      onToggle={handleToggle}
      data-telemetry-id={telemetryId}
    >
      <summary
        className={cn(
          'flex cursor-pointer list-none items-center gap-2 px-3 py-2',
          'hover:bg-secondary/50 rounded-lg group-open:rounded-b-none',
          'group-open:border-b group-open:border-border',
          '[&::-webkit-details-marker]:hidden',
        )}
      >
        <ChevronRight
          aria-hidden
          className="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform group-open:rotate-90"
        />
        {heading}
      </summary>
      <div className="p-3">{children}</div>
    </details>
  );
}
