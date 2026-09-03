import { createClient } from '@/lib/supabase/client';

/**
 * UI interaction telemetry — structural only, never clinical.
 *
 * The question this exists to answer is whether the progressive-disclosure bet
 * in guardrails UI-4 was right. If clinicians expand "At a Glance" within three
 * seconds of every page load, default-closed is costing them time and the data
 * says so. That is worth more than arguing about it.
 *
 * ---------------------------------------------------------------------------
 * WHAT MAY BE SENT, AND WHY IT IS SO NARROW
 *
 * These unions mirror the CHECK constraints on `ui_telemetry` exactly, so an
 * off-schema call is a compile error AND a rejected insert. Two independent
 * barriers, and the one that actually protects the database is the second —
 * a client-side allowlist is a convenience, since a compromised client is not
 * bound by its own types.
 *
 * Component ids are fixed strings from this file. NEVER build one by
 * concatenation, from a prop, from a label, or from element text: that is the
 * path by which a drug name ends up in an analytics column. The DB rejects such
 * a row, but the right place to not send PHI is before the request.
 *
 * The payload carries no user_id, patient_id or care_note_id — the table has no
 * such columns. clinic_id and user_role are stamped server-side by a trigger and
 * are deliberately NOT sent from here; anything this file put there would be
 * overwritten anyway.
 * ---------------------------------------------------------------------------
 */
export type TelemetryComponent =
  | 'sunshine'
  | 'at_a_glance'
  | 'changes_since_last_visit'
  | 'care_plan_completed'
  | 'critical_flags_panel'
  | 'editor_pane'
  | 'timeline'
  | 'retraction_notice';

export type TelemetryAction =
  | 'expand'
  | 'collapse'
  | 'pane_resize'
  | 'scroll_depth'
  | 'dwell'
  | 'view';

const SESSION_KEY = 'ng_telemetry_session';

/**
 * An ephemeral id for "the same sitting", unrelated to auth.uid(), the GoTrue
 * session, the encounter or the patient. It lets the dashboard tell one visit
 * from two without identifying whose visit it was, and it dies with the tab.
 *
 * sessionStorage rather than localStorage so it does not persist across
 * sittings, and wrapped because private mode and blocked site-data both throw
 * on access rather than returning null.
 */
function sessionId(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    let id = window.sessionStorage.getItem(SESSION_KEY);
    if (!id) {
      id = crypto.randomUUID();
      window.sessionStorage.setItem(SESSION_KEY, id);
    }
    return id;
  } catch {
    // Storage unavailable. Telemetry is optional; clinical work is not.
    return null;
  }
}

export interface TelemetryPayload {
  session_uuid: string;
  component_id: TelemetryComponent;
  action: TelemetryAction;
  dwell_ms?: number;
  value_pct?: number;
}

/**
 * Pure, so it can be tested without a database or a browser.
 *
 * Clamps rather than rejects: a backgrounded tab can produce a dwell of hours,
 * and the DB CHECK would reject that row outright. Losing the event is worse
 * than recording a capped one, because the event still says "they opened it".
 */
export function buildPayload(
  session: string,
  component: TelemetryComponent,
  action: TelemetryAction,
  opts: { dwellMs?: number; valuePct?: number } = {},
): TelemetryPayload {
  const payload: TelemetryPayload = {
    session_uuid: session,
    component_id: component,
    action,
  };
  if (typeof opts.dwellMs === 'number' && Number.isFinite(opts.dwellMs)) {
    payload.dwell_ms = Math.max(0, Math.min(3_600_000, Math.round(opts.dwellMs)));
  }
  if (typeof opts.valuePct === 'number' && Number.isFinite(opts.valuePct)) {
    payload.value_pct = Math.max(0, Math.min(100, Math.round(opts.valuePct)));
  }
  return payload;
}

/**
 * Fire and forget. Never awaited by a caller, never blocks a clinical action,
 * and never surfaces an error to the clinician — a failed metric is not
 * something to interrupt a consult for.
 *
 * NOTE THE ABSENT `.select()`. Non-admins may write telemetry but not read it,
 * so chaining .select() would make the insert perform a read-back, fail the
 * SELECT policy, and reject EVERY write — silently, given the catch below. The
 * migration documents this at the table.
 */
export function emit(
  component: TelemetryComponent,
  action: TelemetryAction,
  opts: { dwellMs?: number; valuePct?: number } = {},
): void {
  const session = sessionId();
  if (!session) return;

  try {
    const supabase = createClient();
    void supabase
      .from('ui_telemetry')
      .insert(buildPayload(session, component, action, opts))
      .then(({ error }) => {
        if (error && process.env.NODE_ENV === 'development') {
          // Dev only. In production a telemetry failure is silent by design,
          // but during development a silent failure is how you end up with an
          // empty dashboard that reads as "nobody uses this".
          console.warn('[telemetry] dropped:', error.message);
        }
      });
  } catch {
    // Never let instrumentation break a page.
  }
}
