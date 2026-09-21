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

import { AdversarialInputNotice } from './AdversarialInputNotice';

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
      screen.getByText(/command-like phrasing in source audio/i),
    ).toBeInTheDocument();
  });

  it('uses the uppercase-bold treatment the other degraded states use', () => {
    render(<AdversarialInputNotice />);
    const heading = screen.getByText(/command-like phrasing in source audio/i);
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
