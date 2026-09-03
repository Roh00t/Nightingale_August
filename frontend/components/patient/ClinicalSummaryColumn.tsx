'use client';

import type { ComponentProps } from 'react';
import { ChangesSinceLastVisitCard } from '@/components/glance/ChangesSinceLastVisitCard';
import { SunshineBlock } from '@/components/glance/SunshineBlock';
import { TopCard } from '@/components/glance/TopCard';
import { CollapsibleSection } from '@/components/ui/collapsible-section';
import { DegradedAIPanel } from '@/components/patient/DegradedAIPanel';
import type { ChangeSinceLastVisit } from '@/lib/types';

interface ClinicalSummaryColumnProps {
  /**
   * Null when the contradiction check succeeded.
   *
   * NEVER placed inside a collapsible. An outage that can be folded away is an
   * outage nobody sees, and the whole point of this panel is that "we could not
   * check" must not be mistakable for "nothing found" (guardrails.md UI-1).
   */
  degraded: ComponentProps<typeof DegradedAIPanel> | null;
  /**
   * Props forwarded 1:1 rather than reduced to a `careNote` object, so the diff
   * against the old inline call site is mechanically checkable and so a dropped
   * optional prop is a type error rather than a silent behaviour change.
   *
   * That hazard is real: TopCard renders its whole ActionItems block only when
   * onAssignAction, onCompleteAction AND onDeferAction are all present, and
   * dropping currentNoteVersion makes every critical flag claim its source was
   * edited.
   */
  sunshine: ComponentProps<typeof SunshineBlock>;
  glance: ComponentProps<typeof TopCard>;
  changes: ChangeSinceLastVisit[];
  /**
   * From `hasActiveCriticalAlert`. When true, every section renders with no
   * disclosure control at all — see CollapsibleSection.
   */
  forceOpen: boolean;
  /** From `describeGlanceLoad`. Shown on the closed summary line so closed never reads as empty. */
  glanceSummary: string;
}

export function ClinicalSummaryColumn({
  degraded,
  sunshine,
  glance,
  changes,
  forceOpen,
  glanceSummary,
}: ClinicalSummaryColumnProps) {
  return (
    <>
      {degraded && <DegradedAIPanel {...degraded} />}

      <CollapsibleSection
        title="Sunshine disclosure"
        telemetryId="sunshine"
        subtitle="how much of this is AI, and is it auditable"
        forceOpen={forceOpen}
      >
        <SunshineBlock {...sunshine} />
      </CollapsibleSection>

      <CollapsibleSection
        title="At a Glance"
        subtitle={glanceSummary}
        forceOpen={forceOpen}
        telemetryId="at_a_glance"
      >
        <TopCard {...glance} />
      </CollapsibleSection>

      {changes.length > 0 && (
        <CollapsibleSection
          title="Changes Since Last Visit"
          telemetryId="changes_since_last_visit"
          subtitle={`${changes.length} change${changes.length === 1 ? '' : 's'}`}
          forceOpen={forceOpen}
        >
          <ChangesSinceLastVisitCard changes={changes} bare />
        </CollapsibleSection>
      )}
    </>
  );
}
