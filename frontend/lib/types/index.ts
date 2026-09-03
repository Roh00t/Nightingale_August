// ============================================================
// Core domain types for Nightingale
// ============================================================

export type UserRole = 'patient' | 'staff' | 'clinician' | 'admin';

export type EntryType =
  | 'manual_note'
  | 'ai_doctor_consult_summary'
  | 'ai_nurse_consult_summary'
  | 'ai_patient_session_summary'
  | 'instruction'
  | 'admin'
  | 'system_event'
  | 'patient_message';

export type RiskLevel = 'critical' | 'high' | 'medium' | 'low' | 'info';
export type Visibility = 'internal' | 'patient_visible';

export type ActionType =
  | 'pin' | 'unpin' | 'edit' | 'comment'
  | 'accept' | 'reject' | 'manual_highlight'
  | 'view' | 'dismiss';

// ============================================================
// Database row types
// ============================================================

export interface Clinic {
  id: string;
  name: string;
  settings: Record<string, unknown>;
  created_at: string;
}

export interface Profile {
  id: string;
  clinic_id: string;
  role: UserRole;
  display_name: string;
  avatar_url: string | null;
  created_at: string;
}

export interface CareNote {
  id: string;
  patient_id: string;
  clinic_id: string;
  yjs_state: string | null;
  glance_cache: GlanceCache;
  glance_cache_updated_at: string;
  created_at: string;
  updated_at: string;
  /** Optimistic-concurrency counter; also the provenance anchor for highlights. */
  version?: number | null;
}

export interface GlanceCache {
  top_items: GlanceItem[];
  care_plan_score: number;
  last_visit: string;
  changes_since_last_visit?: ChangeSinceLastVisit[];
  care_plan_items?: CarePlanItem[];
}

export interface GlanceItem {
  type: 'action' | 'risk' | 'positive';
  text: string;
  risk_level: RiskLevel;
  status?: string;
  confidence?: number;
}

export interface TimelineEntry {
  id: string;
  care_note_id: string;
  author_role: string;
  author_id: string | null;
  entry_type: EntryType;
  content: Record<string, unknown>;
  content_text: string | null;
  provenance_pointer: ProvenancePointer | null;
  risk_level: RiskLevel;
  visibility: Visibility;
  metadata: Record<string, unknown>;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
  // Joined fields
  author?: Profile;
  /**
   * A patient message withdrawn after sending. The entry is marked, never
   * deleted: the patient already read it, and a message that silently vanishes
   * is worse than one shown as withdrawn — they remember being told something
   * and can no longer find it, and an auditor cannot reconstruct what was sent.
   */
  is_retracted?: boolean;
  retracted_at?: string | null;
  retracted_by?: string | null;
  retraction_reason?: string | null;
}

export interface NoteVersion {
  id: string;
  care_note_id: string;
  version_number: number;
  yjs_snapshot: string | null;
  content_snapshot: Record<string, unknown> | null;
  changed_by: string | null;
  change_summary: string | null;
  created_at: string;
  // Joined
  author?: Profile;
}

export interface Comment {
  id: string;
  care_note_id: string;
  timeline_entry_id: string | null;
  parent_comment_id: string | null;
  author_id: string;
  author_role: string;
  content: string;
  anchor_data: { from: number; to: number; mark_id: string } | null;
  is_resolved: boolean;
  resolved_by: string | null;
  mentions: string[];
  created_at: string;
  // Joined
  author?: Profile;
  replies?: Comment[];
}

export interface Highlight {
  id: string;
  care_note_id: string;
  source_entry_id: string | null;
  content_snippet: string;
  risk_reason: string;
  risk_level: RiskLevel;
  importance_score: number;
  provenance_pointer: ProvenancePointer | null;
  is_accepted: boolean | null;
  is_pinned: boolean;
  created_by: string;
  created_at: string;
  expires_at: string | null;

  /**
   * Clinical safety layer output. Three DISTINCT quantities — never collapse
   * them into one number on screen:
   *   importance_score  workflow urgency   (queue position)
   *   confidence_score  system reliability (how much to trust the claim)
   *   risk_level        clinical severity  (how bad it is if true)
   */
  confidence_score?: number | null;
  confidence_band?: ConfidenceBand | null;
  /** Level the deterministic rules required, independent of the model. */
  risk_floor?: RiskLevel | null;
  /** Level the model proposed. final risk = max(risk_floor, model_risk). */
  model_risk?: RiskLevel | null;
  /** Confidence fell below the abstention threshold. */
  abstained?: boolean;
  safety_metadata?: SafetyMetadata | null;

  /**
   * Addressable provenance. `care_notes.version` at extraction time, and a
   * sha256 of the normalised supporting quote.
   *
   * Both may be null on highlights created before this was tracked. Null is
   * treated as "changed", not "unchanged" — see isSourceModified below.
   */
  source_note_version?: number | null;
  exact_quote_hash?: string | null;
}

/**
 * Whether a highlight's source has moved since the claim was extracted.
 *
 * Deliberately fails toward "modified". A highlight with no recorded version
 * predates provenance tracking, and there is no way to know whether its source
 * still says what it said — so the UI degrades to a visible "Source Modified"
 * tag rather than asserting a freshness it cannot support. Silently showing it
 * as current is the failure this exists to prevent: the clinician follows the
 * link, reads different text, and concludes the highlight was wrong.
 */
export function isSourceModified(
  highlight: Pick<Highlight, 'source_note_version'>,
  currentNoteVersion: number | null | undefined
): boolean {
  if (highlight.source_note_version == null) return true;
  if (currentNoteVersion == null) return true;
  return highlight.source_note_version !== currentNoteVersion;
}

export type ConfidenceBand = 'high' | 'medium' | 'low';

/** Published numeric meaning of each band, shown to clinicians on hover. */
export const CONFIDENCE_BANDS: Record<ConfidenceBand, string> = {
  high: '≥ 0.85 — consistent across samples and verbatim in the record',
  medium: '0.60–0.84 — mostly consistent; verify before acting',
  low: '< 0.60 — withheld from the glance view; sent for manual review',
};

export interface SafetyMetadata {
  /** Deterministic rules that set the risk floor, with their rationale. */
  triggered_rules?: Array<{ name: string; rationale: string }>;
  /** How the confidence score decomposed. */
  confidence_components?: {
    agreement?: number;
    verification?: number;
    rule_support?: number;
  };
  /** 'exact' | 'normalized' — how the quote matched its source. */
  extraction_verdict?: string;
  /** True when a critical finding is shown despite low confidence. */
  unverified?: boolean;
}

/** One side of a clinical contradiction, quoted verbatim. */
export interface ConflictClaim {
  author_role: string;
  author_id: string | null;
  entry_id: string;
  value: string;
  quote: string;
  timestamp?: string | null;
}

/**
 * A detected contradiction between two authors about the same clinical fact.
 * The system surfaces the delta and never arbitrates: it has no basis to decide
 * which clinician is right, and choosing would manufacture false certainty.
 */
export interface ClinicalConflict {
  conflict_class: 'allergy' | 'dosage' | 'medication' | 'vital';
  entity: string;
  severity: 'critical' | 'high';
  requires_human_resolution: boolean;
  claims: ConflictClaim[];
}

export interface InteractionLog {
  id: string;
  user_id: string;
  user_role: string;
  action_type: ActionType;
  target_type: string;
  target_id: string;
  target_metadata: Record<string, unknown>;
  created_at: string;
}

// ============================================================
// Provenance types
// ============================================================

export interface ProvenancePointer {
  source_type: string;
  source_id: string;
  session_id?: string;
  span?: { from: number; to: number };
}

// ============================================================
// Trust badge types
// ============================================================

export type TrustBadgeType =
  | 'clinician_verified'
  | 'ai_generated'
  | 'patient_reported'
  | 'staff_noted'
  | 'conflict';

export interface TrustBadge {
  type: TrustBadgeType;
  confidence?: number;
  label: string;
}

// ============================================================
// Glance View types
// ============================================================

export interface ChangeSinceLastVisit {
  type: 'new' | 'improved' | 'concerning' | 'unresolved';
  symbol: string;
  text: string;
  detail: string;
}

export interface CarePlanItem {
  label: string;
  completed: boolean;
}

// ============================================================
// AI service types
// ============================================================

export interface SummarizeRequest {
  care_note_id: string;
  entries: Array<{
    id: string;
    content_text: string;
    entry_type: string;
    author_role: string;
    created_at: string;
  }>;
}

export interface SummarizeResponse {
  highlights: Array<{
    content_snippet: string;
    risk_reason: string;
    risk_level: RiskLevel;
    importance_score: number;
    source_entry_id: string;
    provenance_pointer: ProvenancePointer;
  }>;
  changes_since_last_visit: ChangeSinceLastVisit[];
  care_plan_score: number;
  care_plan_items: CarePlanItem[];
  patient_summary: string;
}

// Wire-format types returned by the AI service (/api/ai/summarize)
export interface AICarePlanItem {
  item: string;
  priority: 'high' | 'medium' | 'low';
  status: 'new' | 'ongoing' | 'resolved';
}

export interface AISummarizeResponse {
  care_note_id: string;
  highlights: string[];
  changes_since_last_visit: string[];
  care_plan_score: number;
  care_plan_items: AICarePlanItem[];
  patient_summary: string;
}

export interface RedactRequest {
  text: string;
}

export interface RedactResponse {
  redacted_text: string;
  entities_found: number;
}

/**
 * A `glance_cache` safe to persist.
 *
 * `/patients/[id]` recomposes the clinical assessment into `glance_cache` in
 * memory so downstream components keep one shape. That object is therefore a
 * *display* value, and spreading it into an `update()` writes the assessment
 * into a column patients can read — which is exactly how the leak returned
 * after it had once been fixed.
 *
 * Every write to `care_notes.glance_cache` must go through this. The database
 * also strips these keys on write (`20260901000002_glance_cache_guard.sql`), so this
 * is the readable half of a guarantee that is enforced elsewhere — the app
 * should not depend on the database silently discarding what it sent.
 */
export type PatientSafeGlanceCache = Omit<GlanceCache, 'top_items' | 'changes_since_last_visit'>;

export function patientSafeGlanceCache(
  cache: GlanceCache | null | undefined
): PatientSafeGlanceCache {
  const {
    top_items: _topItems,
    changes_since_last_visit: _changes,
    ...safe
  } = cache ?? ({} as GlanceCache);
  return safe;
}
