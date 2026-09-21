/**
 * @vitest-environment jsdom
 *
 * Call-site wiring for the adversarial-input notice.
 *
 * AdversarialInputNotice.test.tsx proves the notice renders and says the right
 * thing. It explicitly does NOT prove that VoiceCapture passes the flag — that
 * gap was recorded as ABSENT when the notice shipped, and this file closes it.
 * The distinction is not academic: a correct component wired to nothing renders
 * nothing, and the backend flag would still be dying in a log.
 *
 * The whole capture path is driven: record, stop, upload, response. Stubbing
 * `result` directly would prove the JSX compiles, not that the flag survives
 * the round trip through fetch and into state.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import '@testing-library/jest-dom/vitest';

import { VoiceCapture } from './VoiceCapture';
import { TooltipProvider } from '@/components/ui/tooltip';
import type { UserRole } from '@/lib/types';

/** Minimal MediaRecorder the component can drive. jsdom has none. */
class FakeMediaRecorder {
  static isTypeSupported = () => true;
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  state = 'inactive';

  start() {
    this.state = 'recording';
  }

  stop() {
    this.state = 'inactive';
    // A real recorder emits its buffer before onstop. The component builds its
    // Blob from those chunks and refuses to upload an empty one, so a fake that
    // skipped this would exercise the "nothing was recorded" branch instead.
    this.ondataavailable?.({ data: new Blob(['x'], { type: 'audio/webm' }) });
    this.onstop?.();
  }
}

function response(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as Response;
}

const BASE_RESULT = {
  interaction_type: 'doctor_consult',
  entry_type: 'ai_doctor_consult_summary',
  timeline_entry_id: null,
  filed: false,
  summary: 'BP 140/90. Lisinopril continued at 10mg daily.',
  key_points: [],
  redacted_transcript: 'SPEAKER_1: BP is 140 over 90.',
  segments: [],
  speakers: ['SPEAKER_1'],
  transcription: { source: 'mock' },
  redaction: { total_entities: 0 },
};

beforeEach(() => {
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder);
  Object.defineProperty(globalThis.navigator, 'mediaDevices', {
    configurable: true,
    value: {
      getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop: vi.fn() }] })),
    },
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function captureWith(body: unknown, userRole: UserRole = 'clinician') {
  const fetchMock = vi.fn(async () => response(body));
  vi.stubGlobal('fetch', fetchMock);

  render(
    <TooltipProvider>
      <VoiceCapture token="stub-token" userRole={userRole} />
    </TooltipProvider>,
  );

  // Record, then stop. The fake recorder emits its buffer and fires onstop
  // synchronously, which triggers the upload.
  await act(async () => {
    fireEvent.click(screen.getByLabelText('Start recording'));
  });
  await act(async () => {
    fireEvent.click(screen.getByLabelText('Stop recording'));
  });

  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  await waitFor(() => expect(screen.getByText(/lisinopril/i)).toBeInTheDocument());
  return fetchMock;
}

describe('VoiceCapture — passes injection_suspected through to the notice', () => {
  it('renders the notice when the backend flags the capture', async () => {
    await captureWith({ ...BASE_RESULT, injection_suspected: true });
    expect(screen.getByTestId('adversarial-input-notice')).toBeInTheDocument();
  });

  it('names audio, because this path is always speech', async () => {
    await captureWith({ ...BASE_RESULT, injection_suspected: true });
    expect(screen.getByTestId('adversarial-input-notice')).toHaveTextContent(
      /in this recording/i,
    );
  });

  it('still shows the clinical summary alongside the notice', async () => {
    // Non-blocking: detection must never cost the note. A consult cannot be
    // re-recorded.
    await captureWith({ ...BASE_RESULT, injection_suspected: true });
    expect(screen.getByTestId('adversarial-input-notice')).toBeInTheDocument();
    expect(screen.getByText(/BP 140\/90/)).toBeInTheDocument();
  });

  it('does not claim the summary is hallucinated', async () => {
    await captureWith({ ...BASE_RESULT, injection_suspected: true });
    expect(screen.getByTestId('adversarial-input-notice')).not.toHaveTextContent(
      /hallucinat/i,
    );
  });
});

describe('VoiceCapture — when the capture is not flagged', () => {
  it('renders no notice when the flag is false', async () => {
    await captureWith({ ...BASE_RESULT, injection_suspected: false });
    expect(screen.queryByTestId('adversarial-input-notice')).toBeNull();
  });

  it('renders no notice when the backend omits the field', async () => {
    // An older AI service sends nothing. Undefined must not raise an alarm —
    // and equally must not be read as a cleared one.
    await captureWith(BASE_RESULT);
    expect(screen.queryByTestId('adversarial-input-notice')).toBeNull();
  });
});

describe('VoiceCapture — the notice is clinician-facing', () => {
  it('is hidden from a patient recording their own update', async () => {
    await captureWith({ ...BASE_RESULT, injection_suspected: true }, 'patient');
    expect(screen.queryByTestId('adversarial-input-notice')).toBeNull();
  });

  it('is shown to staff', async () => {
    await captureWith({ ...BASE_RESULT, injection_suspected: true }, 'staff');
    expect(screen.getByTestId('adversarial-input-notice')).toBeInTheDocument();
  });
});
