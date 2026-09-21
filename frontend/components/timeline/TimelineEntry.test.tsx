/**
 * @vitest-environment jsdom
 *
 * The adversarial-input flag on a FILED entry.
 *
 * The AI service has written `injection_suspected` into
 * timeline_entries.metadata since the detector shipped, and until now nothing
 * read it back — the signal reached a WARNING log and never a clinician. A
 * security indicator that renders nowhere is indistinguishable from no
 * indicator, and no source assertion can tell those two apart, which is why
 * this file mounts the component.
 *
 * The negative cases matter as much as the positive one. `undefined` metadata
 * means the entry predates the detector: "not checked", not "nothing found".
 * Only the second may ever be implied, and neither may be implied by silence
 * plus a badge that happens to be absent for an unrelated reason.
 */
import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import '@testing-library/jest-dom/vitest';

import { TimelineEntry } from './TimelineEntry';
import { TooltipProvider } from '@/components/ui/tooltip';
import type { TimelineEntry as TimelineEntryType, UserRole } from '@/lib/types';

afterEach(cleanup);

function makeEntry(metadata: Record<string, unknown>): TimelineEntryType {
  return {
    id: 'entry-1',
    care_note_id: 'note-1',
    author_role: 'system',
    author_id: null,
    entry_type: 'ai_doctor_consult_summary',
    content: {},
    content_text: 'BP 140/90. Lisinopril continued at 10mg daily.',
    provenance_pointer: null,
    risk_level: 'info',
    visibility: 'internal',
    metadata,
    is_archived: false,
    created_at: '2026-09-21T09:00:00Z',
    updated_at: '2026-09-21T09:00:00Z',
  } as TimelineEntryType;
}

function renderEntry(
  metadata: Record<string, unknown>,
  userRole: UserRole = 'clinician',
) {
  // TrustBadge renders a Radix Tooltip, which throws outside a provider. The
  // app supplies one at app/layout.tsx:40, so mounting without it would be
  // testing a configuration that never ships.
  return render(
    <TooltipProvider>
      <TimelineEntry
        entry={makeEntry(metadata)}
        comments={[]}
        isHighlighted={false}
        userRole={userRole}
        onAddComment={() => {}}
        onNavigateToSource={() => {}}
      />
    </TooltipProvider>,
  );
}

const FLAGGED = {
  injection_suspected: true,
  injection_signal_fields: ['transcript'],
};

describe('TimelineEntry — adversarial input telemetry', () => {
  it('renders the badge when the filed entry carries the flag', () => {
    renderEntry(FLAGGED);
    expect(screen.getByTestId('injection-badge')).toBeInTheDocument();
  });

  it('states the condition in words on the badge itself', () => {
    // UI-1: never signalled by colour alone.
    renderEntry(FLAGGED);
    expect(screen.getByTestId('injection-badge')).toHaveTextContent(
      /directive phrasing in source/i,
    );
  });

  it('renders the full notice in the entry detail disclosure', () => {
    renderEntry(FLAGGED);
    expect(screen.getByTestId('adversarial-input-notice')).toBeInTheDocument();
  });

  it('names audio when the flag came from a transcript', () => {
    renderEntry(FLAGGED);
    expect(screen.getByTestId('adversarial-input-notice')).toHaveTextContent(
      /in this recording/i,
    );
  });

  it('does not name audio when the flag came from typed input', () => {
    // /summarize sets the same flag from typed entries and patient_context.
    // Telling a clinician to review "this recording" would send them hunting
    // for audio that does not exist.
    renderEntry({
      injection_suspected: true,
      injection_signal_fields: ['patient_context'],
    });
    const notice = screen.getByTestId('adversarial-input-notice');
    expect(notice).not.toHaveTextContent(/in this recording/i);
    expect(notice).toHaveTextContent(/written in a form/i);
  });

  it('never claims the note is hallucinated', () => {
    renderEntry(FLAGGED);
    expect(screen.getByTestId('adversarial-input-notice')).not.toHaveTextContent(
      /hallucinat/i,
    );
  });

  it('still renders the clinical content in full', () => {
    // Non-blocking means the note is not hidden, truncated or gated.
    const { container } = renderEntry(FLAGGED);
    expect(container).toHaveTextContent(/Lisinopril continued at 10mg daily/);
  });

  it('adds no dismiss control', () => {
    renderEntry(FLAGGED);
    const notice = screen.getByTestId('adversarial-input-notice');
    expect(within(notice).queryByRole('button')).toBeNull();
  });
});

describe('TimelineEntry — when the flag is absent or false', () => {
  it('renders nothing for metadata without the field', () => {
    renderEntry({});
    expect(screen.queryByTestId('injection-badge')).toBeNull();
    expect(screen.queryByTestId('adversarial-input-notice')).toBeNull();
  });

  it('renders nothing when the flag is explicitly false', () => {
    renderEntry({ injection_suspected: false });
    expect(screen.queryByTestId('injection-badge')).toBeNull();
    expect(screen.queryByTestId('adversarial-input-notice')).toBeNull();
  });

  it('is not fooled by a truthy non-boolean', () => {
    // Strict === true. A stringified "false" from a loose serialiser must not
    // raise an alarm, and neither must any other truthy junk.
    for (const value of ['false', 'true', 1, {}, []]) {
      renderEntry({ injection_suspected: value });
      expect(screen.queryByTestId('injection-badge')).toBeNull();
      cleanup();
    }
  });
});

describe('TimelineEntry — the notice is clinician-facing', () => {
  it('is hidden from a patient viewing their own timeline', () => {
    // Same reasoning as the ASR diagnostics: not actionable for a patient, and
    // where a patient's own words carried the phrasing they are the author.
    renderEntry(FLAGGED, 'patient');
    expect(screen.queryByTestId('injection-badge')).toBeNull();
    expect(screen.queryByTestId('adversarial-input-notice')).toBeNull();
  });

  it.each(['clinician', 'staff', 'admin'] as UserRole[])(
    'is shown to %s',
    (role) => {
      renderEntry(FLAGGED, role);
      expect(screen.getByTestId('injection-badge')).toBeInTheDocument();
    },
  );
});
