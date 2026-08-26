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
