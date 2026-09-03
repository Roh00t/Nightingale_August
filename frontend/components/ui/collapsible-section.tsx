'use client';

import { ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';

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
  children: React.ReactNode;
  className?: string;
}

export function CollapsibleSection({
  title,
  subtitle,
  forceOpen = false,
  children,
  className,
}: CollapsibleSectionProps) {
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
    <details className={cn('group rounded-lg border border-border bg-card', className)}>
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
