/**
 * The single way this app talks to the FastAPI service.
 *
 * Every call here used to be a bare `fetch` with no timeout. `fetch` has no
 * default one, so a FastAPI process that accepted the connection and then
 * stalled — a Groq call hanging behind a rate limit, a container being
 * rescheduled — left the promise pending forever. The UI showed a spinner with
 * no end state, and the clinician's only recourse was reloading the page and
 * losing whatever they had typed.
 *
 * A hung request and a slow one are indistinguishable from the browser, so the
 * only honest treatment is a deadline. 25s is chosen to sit under the common
 * 30s edge/proxy timeout: exceeding that returns an HTML error page rather than
 * JSON, and `response.json()` then throws a parse error that reads like a bug
 * in this code instead of a timeout.
 */

export const AI_TIMEOUT_MS = 25_000;

export type AIFailureKind = 'timeout' | 'unavailable' | 'unauthorized' | 'rejected' | 'unknown';

export class AIServiceError extends Error {
  readonly kind: AIFailureKind;
  readonly status: number | null;
  /** Parsed `detail` from FastAPI, when it sent structured content. */
  readonly detail: unknown;

  constructor(kind: AIFailureKind, message: string, status: number | null = null, detail: unknown = null) {
    super(message);
    this.name = 'AIServiceError';
    this.kind = kind;
    this.status = status;
    this.detail = detail;
  }

  /**
   * Whether the caller should fall back to rule-derived output.
   *
   * A timeout or an outage means the AI could not answer and the UI should show
   * something deterministic instead. `rejected` must NOT fall back: a 422 from
   * the patient-message gate is a considered refusal, and quietly substituting
   * a rule-derived message would send the patient something no one screened.
   */
  get shouldDegrade(): boolean {
    return this.kind === 'timeout' || this.kind === 'unavailable';
  }
}

const BASE = process.env.NEXT_PUBLIC_AI_SERVICE_URL || 'http://localhost:8000';

export async function callAI<T>(
  path: string,
  body: unknown,
  token: string,
  { timeoutMs = AI_TIMEOUT_MS, signal }: { timeoutMs?: number; signal?: AbortSignal } = {}
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  // Honour a caller's own signal (component unmount) as well as the deadline,
  // without losing which one fired — an unmount is not a service failure and
  // should not raise a timeout banner at the user.
  const onExternalAbort = () => controller.abort();
  signal?.addEventListener('abort', onExternalAbort);

  try {
    const res = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    if (res.ok) return (await res.json()) as T;

    let detail: unknown = null;
    try {
      detail = (await res.json())?.detail ?? null;
    } catch {
      // Non-JSON error body — a proxy error page, most likely. Keep null.
    }

    if (res.status === 401 || res.status === 403) {
      throw new AIServiceError('unauthorized', 'Not authorised for this action.', res.status, detail);
    }
    if (res.status === 422) {
      // A deliberate refusal carrying structured reasons. The caller renders them.
      throw new AIServiceError('rejected', 'The request was refused.', res.status, detail);
    }
    if (res.status === 503 || res.status === 502 || res.status === 504) {
      throw new AIServiceError('unavailable', 'The AI service is unavailable.', res.status, detail);
    }
    throw new AIServiceError('unknown', `AI service returned ${res.status}.`, res.status, detail);
  } catch (err) {
    if (err instanceof AIServiceError) throw err;
    if (signal?.aborted) {
      throw new AIServiceError('unknown', 'Request cancelled.');
    }
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new AIServiceError('timeout', `No response within ${Math.round(timeoutMs / 1000)}s.`);
    }
    // fetch rejects on DNS failure, connection refused, offline — the service
    // is not reachable at all, which is the same clinical situation as a 503.
    throw new AIServiceError('unavailable', 'Could not reach the AI service.');
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', onExternalAbort);
  }
}
