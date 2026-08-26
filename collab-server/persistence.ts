import { getSupabaseAdmin, type UserProfile } from "./auth.js";

// ----------------------------------------------------------------
// Types
// ----------------------------------------------------------------

/** Row shape returned when loading a care note. */
interface CareNoteRow {
  id: string;
  clinic_id: string;
  yjs_state: string | null; // base64-encoded bytea from PostgREST
}

// ----------------------------------------------------------------
// Document name parsing
// ----------------------------------------------------------------

/**
 * Extract the `care_note_id` from the Hocuspocus document name.
 *
 * Document names follow the format `care-note:{uuid}`.
 */
export function parseCareNoteId(documentName: string): string {
  const prefix = "care-note:";
  if (!documentName.startsWith(prefix)) {
    throw new Error(
      `Invalid document name format: "${documentName}". Expected "care-note:{uuid}".`
    );
  }

  const id = documentName.slice(prefix.length);
  if (!id || id.length < 36) {
    throw new Error(
      `Invalid care_note_id in document name: "${documentName}".`
    );
  }

  return id;
}

// ----------------------------------------------------------------
// Clinic scope verification
// ----------------------------------------------------------------

/**
 * Verify the user belongs to the same clinic as the care note.
 * Throws if the user's `clinic_id` does not match.
 */
export async function verifyCareNoteClinicScope(
  careNoteId: string,
  userProfile: UserProfile
): Promise<CareNoteRow> {
  const supabase = getSupabaseAdmin();

  const { data, error } = await supabase
    .from("care_notes")
    .select("id, clinic_id, yjs_state")
    .eq("id", careNoteId)
    .single();

  if (error || !data) {
    throw new Error(
      `Care note ${careNoteId} not found: ${error?.message ?? "no data"}`
    );
  }

  const row = data as CareNoteRow;

  if (row.clinic_id !== userProfile.clinic_id) {
    throw new Error(
      `Access denied: user clinic ${userProfile.clinic_id} does not match care note clinic ${row.clinic_id}`
    );
  }

  return row;
}

// ----------------------------------------------------------------
// Load Yjs state
// ----------------------------------------------------------------

/**
 * Load the persisted Yjs state (binary) from the `care_notes` table.
 *
 * Returns `null` if the note has no prior Yjs state (brand-new note).
 *
 * PostgREST returns `bytea` columns as base64-encoded strings, so we
 * decode that into a `Uint8Array`.
 */
export async function loadYjsState(
  careNoteId: string
): Promise<Uint8Array | null> {
  const supabase = getSupabaseAdmin();

  const { data, error } = await supabase
    .from("care_notes")
    .select("yjs_state")
    .eq("id", careNoteId)
    .single();

  if (error || !data) {
    console.warn(
      `[persistence] Could not load yjs_state for ${careNoteId}:`,
      error?.message
    );
    return null;
  }

  const encoded = (data as { yjs_state: string | null }).yjs_state;
  if (!encoded) return null;

  // PostgREST returns bytea as a hex-prefixed string (\x...) or base64
  // depending on the Accept header. The JS client defaults to base64.
  try {
    const buffer = Buffer.from(encoded, "base64");
    return new Uint8Array(buffer);
  } catch {
    console.error(
      `[persistence] Failed to decode yjs_state for ${careNoteId}`
    );
    return null;
  }
}

// ----------------------------------------------------------------
// Save Yjs state
// ----------------------------------------------------------------

/**
 * Persist the full Yjs document state back to the `care_notes` table.
 *
 * The binary state is base64-encoded before being sent to PostgREST.
 */
export async function saveYjsState(
  careNoteId: string,
  state: Uint8Array
): Promise<void> {
  const supabase = getSupabaseAdmin();

  const base64 = Buffer.from(state).toString("base64");

  const { error } = await supabase
    .from("care_notes")
    .update({
      yjs_state: base64,
      updated_at: new Date().toISOString(),
    })
    .eq("id", careNoteId);

  if (error) {
    console.error(
      `[persistence] Failed to save yjs_state for ${careNoteId}:`,
      error.message
    );
    throw error;
  }

  console.log(`[persistence] Saved yjs_state for ${careNoteId}`);
}

// ----------------------------------------------------------------
// Create note version snapshot
// ----------------------------------------------------------------

/**
 * Insert a new `note_versions` row as a point-in-time snapshot.
 *
 * The `version_number` is determined by counting existing versions + 1.
 * A `content_snapshot` (JSON representation of the Yjs document) can be
 * supplied for human-readable diffs; pass `null` if unavailable.
 */
export async function createNoteVersion(
  careNoteId: string,
  yjsSnapshot: Uint8Array,
  changedBy: string,
  contentSnapshot: Record<string, unknown> | null = null,
  changeSummary: string = "Auto-saved version"
): Promise<void> {
  const supabase = getSupabaseAdmin();

  // Determine next version number
  const { data: latestVersion, error: versionError } = await supabase
    .from("note_versions")
    .select("version_number")
    .eq("care_note_id", careNoteId)
    .order("version_number", { ascending: false })
    .limit(1)
    .maybeSingle();

  if (versionError) {
    console.error(
      `[persistence] Failed to query latest version for ${careNoteId}:`,
      versionError.message
    );
    throw versionError;
  }

  const nextVersion = latestVersion ? latestVersion.version_number + 1 : 1;

  const base64Snapshot = Buffer.from(yjsSnapshot).toString("base64");

  const { error: insertError } = await supabase
    .from("note_versions")
    .insert({
      care_note_id: careNoteId,
      version_number: nextVersion,
      yjs_snapshot: base64Snapshot,
      content_snapshot: contentSnapshot,
      changed_by: changedBy,
      change_summary: changeSummary,
    });

  if (insertError) {
    console.error(
      `[persistence] Failed to create version ${nextVersion} for ${careNoteId}:`,
      insertError.message
    );
    throw insertError;
  }

  console.log(
    `[persistence] Created version ${nextVersion} for ${careNoteId} by ${changedBy}`
  );
}
