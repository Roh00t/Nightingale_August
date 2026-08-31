'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Mic, Square, Loader2, AlertTriangle, FileAudio, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { aiUrl } from '@/lib/ai_client';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import type { UserRole } from '@/lib/types';

/**
 * Ambient consult capture.
 *
 * Records audio in the browser, uploads it once, and renders the structured
 * clinical summary that comes back. PHI is stripped server-side before any LLM
 * sees the transcript — this component never sees an unredacted transcript,
 * because the API does not return one.
 *
 * Three guardrails are load-bearing rather than cosmetic:
 *
 *   120-second hard stop. Enforced by a timer the user cannot extend. Audio is
 *   metered downstream, and a recording left running by accident is the easiest
 *   way to turn a demo into a bill. It also keeps every upload under the 5MB
 *   limit the server enforces independently.
 *
 *   Single-flight. The button is disabled from the moment upload begins until
 *   the response lands, and an in-flight ref blocks re-entry even if a click
 *   slips through. A double-click would otherwise transcribe the same audio
 *   twice, at full cost.
 *
 *   Explicit teardown. Every microphone track is stopped on unmount and on
 *   error, so the browser's recording indicator never outlives the component.
 */

const MAX_RECORDING_SECONDS = 120;

interface TranscriptSegment {
  speaker: string;
  text: string;
  start: number | null;
  end: number | null;
  confidence: number | null;
}

interface TranscribeResult {
  interaction_type: string;
  entry_type: string;
  timeline_entry_id: string | null;
  filed: boolean;
  summary: string;
  key_points: string[];
  redacted_transcript: string;
  segments: TranscriptSegment[];
  speakers: string[];
  transcription: {
    source?: string;
    model_id?: string;
    speaker_count?: number;
    segment_count?: number;
    mean_confidence?: number | null;
  };
  redaction: {
    total_entities?: number;
    entity_counts?: Record<string, number>;
  };
}

interface VoiceCaptureProps {
  token: string;
  userRole: UserRole;
  /**
   * File the summary to this care note.
   *
   * The write happens server-side, with the service-role key. It cannot happen
   * in the browser: every INSERT policy on timeline_entries requires
   * `author_id = auth.uid()`, and an AI-scribed entry is author_role='system'
   * with author_id=NULL — impossible for any role to satisfy, which is the
   * policy working correctly rather than a gap.
   */
  careNoteId?: string;
  /** Called with the summary so the caller can write it to the timeline. */
  onSummary?: (result: TranscribeResult) => void | Promise<void>;
}

/**
 * Capture mode follows the role, and the server enforces it independently.
 * The brief scopes patient voice capture to the patient view; a UI-only check
 * would not be enforcement.
 */
function interactionTypeFor(role: UserRole): string {
  if (role === 'patient') return 'patient_session';
  if (role === 'staff') return 'nurse_consult';
  return 'doctor_consult';
}

function pickMimeType(): string {
  // Chrome yields webm/opus; Safari only offers mp4. An empty string lets the
  // browser choose, which is correct rather than a fallback failure.
  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/mpeg'];
  if (typeof MediaRecorder === 'undefined') return '';
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) ?? '';
}

export function VoiceCapture({ token, userRole, careNoteId, onSummary }: VoiceCaptureProps) {
  const [recording, setRecording] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState<TranscribeResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [supported, setSupported] = useState(true);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const autoStopRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Blocks re-entry even if a click lands before React re-renders the disabled
  // state. `processing` alone is a render-cycle behind.
  const inFlightRef = useRef(false);

  useEffect(() => {
    setSupported(
      typeof navigator !== 'undefined' &&
        !!navigator.mediaDevices?.getUserMedia &&
        typeof MediaRecorder !== 'undefined',
    );
  }, []);

  const releaseMicrophone = useCallback(() => {
    if (autoStopRef.current) { clearTimeout(autoStopRef.current); autoStopRef.current = null; }
    if (tickRef.current) { clearInterval(tickRef.current); tickRef.current = null; }
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    recorderRef.current = null;
  }, []);

  // The microphone must never outlive the component.
  useEffect(() => releaseMicrophone, [releaseMicrophone]);

  const upload = useCallback(async (blob: Blob) => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setProcessing(true);
    setError(null);

    try {
      const form = new FormData();
      const extension = blob.type.includes('mp4') ? 'mp4' : 'webm';
      form.append('audio', blob, `consult.${extension}`);

      const params = new URLSearchParams({ interaction_type: interactionTypeFor(userRole) });
      // Filing is server-side; see careNoteId on the props above.
      if (careNoteId) params.set('care_note_id', careNoteId);
      // aiUrl(), not a second hand-rolled concatenation. This component built
      // its own URL from the same env var, so a trailing slash in the Vercel
      // dashboard broke it independently of lib/ai_client.ts — and a fix
      // applied to one would have left the other silently 404ing.
      const url = aiUrl('transcribe', params);

      // A deliberately longer deadline than the 25s used for JSON calls in
      // lib/ai_client.ts. This request uploads up to 5MB and then waits on
      // speech-to-text over a two-minute recording; 25s would abort work that
      // was going to succeed, and the user would have to re-record audio that
      // cannot be reproduced — the consult already happened.
      //
      // It still has a bound. Without one, a stalled upload leaves the
      // recording in a component state that is lost on navigation, with the
      // clinician watching a spinner and no way to tell whether to wait.
      const TRANSCRIBE_TIMEOUT_MS = 120_000;
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), TRANSCRIBE_TIMEOUT_MS);

      let response: Response;
      try {
        response = await fetch(url, {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
          body: form,
          signal: controller.signal,
        });
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          throw new Error(
            'Transcription timed out after two minutes. The recording is still ' +
            'here — try filing it again, or type the note directly.',
          );
        }
        throw new Error('Could not reach the AI service. The recording is still here.');
      } finally {
        clearTimeout(timer);
      }

      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        // 413 is the recording cap, which the user can act on; say so plainly
        // rather than surfacing a status code.
        throw new Error(
          response.status === 413
            ? 'That recording is too large. Keep it under two minutes.'
            : detail?.detail || `Transcription failed (${response.status})`,
        );
      }

      const data: TranscribeResult = await response.json();
      setResult(data);
      await onSummary?.(data);
      toast.success(
        data.filed
          ? 'Consult transcribed and added to the timeline'
          : 'Consult transcribed and summarised',
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Transcription failed';
      setError(message);
      toast.error(message);
    } finally {
      inFlightRef.current = false;
      setProcessing(false);
    }
  }, [token, userRole, careNoteId, onSummary]);

  const stop = useCallback(() => {
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop();
    setRecording(false);
  }, []);

  const start = useCallback(async () => {
    if (inFlightRef.current || recording || processing) return;

    setError(null);
    setResult(null);
    chunksRef.current = [];

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch {
      const message = 'Microphone access was declined. Enable it in your browser settings.';
      setError(message);
      toast.error(message);
      return;
    }

    streamRef.current = stream;
    const mimeType = pickMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    recorderRef.current = recorder;

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data);
    };

    recorder.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: mimeType || 'audio/webm' });
      releaseMicrophone();
      setElapsed(0);
      if (blob.size === 0) {
        setError('Nothing was recorded. Check your microphone and try again.');
        return;
      }
      void upload(blob);
    };

    recorder.start();
    setRecording(true);
    setElapsed(0);

    tickRef.current = setInterval(() => setElapsed((s) => s + 1), 1000);

    // Hard cap. Not a suggestion — the user cannot extend it.
    autoStopRef.current = setTimeout(() => {
      toast.info(`Recording stopped automatically at ${MAX_RECORDING_SECONDS} seconds`);
      stop();
    }, MAX_RECORDING_SECONDS * 1000);
  }, [recording, processing, releaseMicrophone, upload, stop]);

  if (!supported) {
    return (
      <Card>
        <CardContent className="flex items-start gap-2 pt-4 pb-3 text-xs text-muted-foreground">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <p>
            This browser does not support audio capture. Ambient capture needs a
            recent Chrome, Edge or Safari on a secure (HTTPS) origin.
          </p>
        </CardContent>
      </Card>
    );
  }

  const remaining = MAX_RECORDING_SECONDS - elapsed;
  const busy = recording || processing;

  return (
    <Card>
      <CardContent className="space-y-3 pt-4 pb-3">
        <div className="flex items-center gap-2">
          <FileAudio className="h-4 w-4 shrink-0 text-primary" />
          <h3 className="text-sm font-semibold">
            {userRole === 'patient' ? 'Record a session' : 'Ambient consult capture'}
          </h3>
          <Badge variant="secondary" className="ml-auto text-[10px]">
            max {MAX_RECORDING_SECONDS}s
          </Badge>
        </div>

        <div className="flex items-center gap-2">
          <Button
            size="sm"
            className="gap-2"
            variant={recording ? 'destructive' : 'default'}
            onClick={recording ? stop : start}
            // Disabled throughout upload and transcription: a second click
            // would transcribe the same audio again, at full cost.
            disabled={processing}
            aria-label={recording ? 'Stop recording' : 'Start recording'}
            aria-pressed={recording}
          >
            {processing ? (
              <><Loader2 className="h-3.5 w-3.5 animate-spin" /> Transcribing…</>
            ) : recording ? (
              <><Square className="h-3.5 w-3.5" /> Stop</>
            ) : (
              <><Mic className="h-3.5 w-3.5" /> Record</>
            )}
          </Button>

          {recording && (
            <span
              className="flex items-center gap-1.5 text-xs tabular-nums text-muted-foreground"
              role="timer"
              aria-live="polite"
            >
              <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" aria-hidden />
              {String(Math.floor(elapsed / 60)).padStart(2, '0')}:
              {String(elapsed % 60).padStart(2, '0')}
              <span className={remaining <= 15 ? 'text-red-600' : ''}>
                ({remaining}s left)
              </span>
            </span>
          )}
        </div>

        <p className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
          <ShieldCheck className="mt-0.5 h-3 w-3 shrink-0 text-emerald-600" />
          Audio is transcribed with speaker labels, then names, NRIC and phone
          numbers are removed before any AI model sees the text.
        </p>

        {error && (
          <p role="alert" className="rounded-md border border-red-200 bg-red-50 px-2.5 py-1.5 text-xs text-red-700">
            {error}
          </p>
        )}

        {result && (
          <div className="space-y-2 border-t border-border pt-2" aria-live="polite">
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge variant="secondary" className="text-[10px]">
                {result.transcription.speaker_count ?? result.speakers.length} speakers
              </Badge>
              <Badge variant="secondary" className="text-[10px]">
                {result.transcription.segment_count ?? result.segments.length} segments
              </Badge>
              {typeof result.transcription.mean_confidence === 'number' && (
                <Badge variant="secondary" className="text-[10px]">
                  ASR confidence {Math.round(result.transcription.mean_confidence * 100)}%
                </Badge>
              )}
              {(result.redaction.total_entities ?? 0) > 0 && (
                <Badge variant="secondary" className="bg-emerald-50 text-[10px] text-emerald-700">
                  {result.redaction.total_entities} identifiers removed
                </Badge>
              )}
              {result.filed && (
                <Badge variant="secondary" className="bg-emerald-50 text-[10px] text-emerald-700">
                  filed to timeline
                </Badge>
              )}
              {result.transcription.source === 'mock' && (
                <Badge variant="secondary" className="bg-amber-50 text-[10px] text-amber-700">
                  mock transcript
                </Badge>
              )}
            </div>

            <div>
              <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                Clinical summary
              </p>
              <p className="text-sm leading-relaxed">{result.summary}</p>
            </div>

            {result.key_points.length > 0 && (
              <ul className="space-y-0.5 text-xs text-muted-foreground">
                {result.key_points.map((point, i) => (
                  <li key={i} className="flex items-start gap-1.5">
                    <span className="mt-0.5 text-primary">&#8226;</span>
                    {point}
                  </li>
                ))}
              </ul>
            )}

            <details className="text-xs">
              <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                Speaker-labelled transcript (PHI removed)
              </summary>
              <div className="mt-1.5 space-y-1">
                {result.segments.map((segment, i) => (
                  <p key={i} className="leading-snug">
                    <span className="font-medium text-primary">{segment.speaker}:</span>{' '}
                    <span className="text-muted-foreground">{segment.text}</span>
                    {typeof segment.start === 'number' && (
                      <span className="ml-1 tabular-nums text-[10px] text-muted-foreground/70">
                        [{segment.start.toFixed(1)}s]
                      </span>
                    )}
                  </p>
                ))}
              </div>
            </details>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
