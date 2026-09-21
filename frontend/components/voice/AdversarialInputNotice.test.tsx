/**
 * @vitest-environment jsdom
 *
 * The first test in this repo that actually mounts a React component.
 *
 * Everything else pinning UI here is a source assertion — CLAUDE.md §12b says
 * so plainly: "ABSENT: any harness that renders the portal". Those assertions
 * prove a control is written down, not that a browser honours it. This file
 * exists because the adversarial-input notice is the one place where the gap
 * between "in the code" and "on the screen" is the whole point: a security
 * signal that renders nowhere is the same as no signal, and a source grep
 * cannot tell those apart.
 *
 * What is NOT claimed: this proves the component renders and that its copy says
 * what it is supposed to say. It does not prove VoiceCapture passes the flag
 * correctly — that gating lives at the call site and is pinned separately in
 * test_audit_boundaries.py.
 */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import '@testing-library/jest-dom/vitest';

import {
  AdversarialInputNotice,
  inputSourceFromMetadata,
} from './AdversarialInputNotice';

afterEach(cleanup);

describe('AdversarialInputNotice', () => {
  it('renders as an alert so assistive tech announces it', () => {
    render(<AdversarialInputNotice />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('states the condition in words, not by colour alone', () => {
    // CLAUDE.md §6b: "Every degraded state is stated in words, never implied
    // by a colour or an absence."
    render(<AdversarialInputNotice />);
    expect(
      screen.getByText(/command-like phrasing in source input/i),
    ).toBeInTheDocument();
  });

  it('uses the uppercase-bold treatment the other degraded states use', () => {
    render(<AdversarialInputNotice />);
    const heading = screen.getByText(/command-like phrasing in source input/i);
    expect(heading.className).toContain('uppercase');
    expect(heading.className).toContain('font-bold');
  });

  it('tells the reader the note was still produced', () => {
    // The notice must not read as "output withheld". Nothing was blocked, and
    // a clinician who thinks the summary is missing will go looking for it.
    render(<AdversarialInputNotice />);
    expect(screen.getByRole('alert')).toHaveTextContent(/still produced/i);
  });

  it('does not claim the summary is hallucinated', () => {
    // Injection and hallucination are different failures with different
    // remedies. Sending a clinician to look for invented content when the risk
    // is an injected directive sends them after the wrong thing.
    render(<AdversarialInputNotice />);
    expect(screen.getByRole('alert')).not.toHaveTextContent(/hallucinat/i);
  });

  it('does not claim the model obeyed the phrasing', () => {
    render(<AdversarialInputNotice />);
    expect(screen.getByRole('alert')).toHaveTextContent(/not followed/i);
  });

  it('renders no form control, button or link', () => {
    // Non-blocking means exactly that: nothing to dismiss, nothing to confirm,
    // no path by which the notice can gate the clinician's next action.
    const { container } = render(<AdversarialInputNotice />);
    expect(container.querySelectorAll('button, a, input, select, textarea'))
      .toHaveLength(0);
  });

  it('is presentational — mounting it touches no document state', () => {
    // It renders beside a Yjs editor in some call sites. A component that
    // mutated shared state on mount would corrupt a clinician's in-flight edit,
    // so the guarantee is that mounting is inert and repeatable.
    const before = document.body.innerHTML;
    const first = render(<AdversarialInputNotice />);
    const rendered = first.container.innerHTML;
    cleanup();
    expect(document.body.innerHTML).toBe(before);

    const second = render(<AdversarialInputNotice />);
    expect(second.container.innerHTML).toBe(rendered);
  });
});

describe('AdversarialInputNotice — it names the right medium', () => {
  it('says "recording" for the audio paths', () => {
    render(<AdversarialInputNotice source="audio" />);
    expect(screen.getByRole('alert')).toHaveTextContent(/in this recording/i);
  });

  it('does not say "recording" for typed input', () => {
    // /summarize sets the same flag from typed timeline entries and from
    // patient_context. Telling a clinician to review "this recording" would
    // send them looking for audio that was never captured.
    render(<AdversarialInputNotice source="text" />);
    const alert = screen.getByRole('alert');
    expect(alert).not.toHaveTextContent(/recording/i);
    expect(alert).toHaveTextContent(/written in a form/i);
  });

  it('stays generic when the medium is unknown', () => {
    // Entries flagged before injection_signal_fields existed. Guessing a
    // medium would be a confident claim built on nothing.
    render(<AdversarialInputNotice source="unknown" />);
    const alert = screen.getByRole('alert');
    expect(alert).not.toHaveTextContent(/recording/i);
    expect(alert).toHaveTextContent(/source material/i);
  });

  it('defaults to audio', () => {
    render(<AdversarialInputNotice />);
    expect(screen.getByRole('alert')).toHaveTextContent(/in this recording/i);
  });

  it('never claims hallucination in any variant', () => {
    for (const source of ['audio', 'text', 'unknown'] as const) {
      const { container } = render(<AdversarialInputNotice source={source} />);
      expect(container.textContent ?? '').not.toMatch(/hallucinat/i);
      cleanup();
    }
  });
});

describe('inputSourceFromMetadata', () => {
  it('reads audio from a transcript signal', () => {
    expect(
      inputSourceFromMetadata({ injection_signal_fields: ['transcript'] }),
    ).toBe('audio');
  });

  it('reads text from a typed-entry signal', () => {
    expect(
      inputSourceFromMetadata({ injection_signal_fields: ['entry[2]'] }),
    ).toBe('text');
    expect(
      inputSourceFromMetadata({ injection_signal_fields: ['patient_context'] }),
    ).toBe('text');
  });

  it('prefers audio when a capture carries both', () => {
    expect(
      inputSourceFromMetadata({
        injection_signal_fields: ['entry[0]', 'transcript'],
      }),
    ).toBe('audio');
  });

  it('returns unknown rather than guessing', () => {
    // Every one of these is "we were not told", which is not "it was typed".
    expect(inputSourceFromMetadata(undefined)).toBe('unknown');
    expect(inputSourceFromMetadata(null)).toBe('unknown');
    expect(inputSourceFromMetadata({})).toBe('unknown');
    expect(inputSourceFromMetadata({ injection_signal_fields: [] })).toBe('unknown');
    expect(inputSourceFromMetadata({ injection_signal_fields: 'transcript' })).toBe(
      'unknown',
    );
  });
});
