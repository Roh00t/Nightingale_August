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
// Role + clinic scope verification
// ----------------------------------------------------------------

/**
 * Roles permitted to open a care-note document over the WebSocket, and the
 * access mode each one gets.
 *
 * `patient` is absent by design. This path uses the service-role Supabase
 * client, which bypasses RLS entirely, so none of the patient restrictions
 * enforced in 001_foundation.sql apply here. Before this allowlist existed the
 * only check was clinic membership, which meant any authenticated patient could
 * connect to `care-note:{any_uuid}` in their own clinic and read and write the
 * full internal note. See guardrails.md S2 and S3.
 */
const COLLAB_WRITE_ROLES = ["clinician", "staff"] as const;
const COLLAB_READ_ONLY_ROLES = ["admin"] as const;

export type CollabAccessMode = "write" | "read-only";

/** Result of a successful authorization check. */
export interface CareNoteAccess {
  careNote: CareNoteRow;
  mode: CollabAccessMode;
}

/**
 * Resolve the access mode for a role, or throw if the role has no collaborative
 * access at all. Rejection is explicit rather than a fall-through default.
 */
export function resolveCollabAccessMode(
  role: UserProfile["role"]
): CollabAccessMode {
  if ((COLLAB_WRITE_ROLES as readonly string[]).includes(role)) {
    return "write";
  }
  if ((COLLAB_READ_ONLY_ROLES as readonly string[]).includes(role)) {
    return "read-only";
  }
  throw new Error(
    `Access denied: role "${role}" may not open care note documents`
  );
}

/**
 * Authorize a connection: the user's role must permit collaborative access AND
 * the care note must belong to the user's clinic. Both conditions are required
 * — neither is sufficient alone.
 */
export async function verifyCareNoteClinicScope(
  careNoteId: string,
  userProfile: UserProfile
): Promise<CareNoteAccess> {
  // Role first: a patient is rejected before any record is fetched, so an
  // unauthorized role cannot probe for the existence of a care note id.
  const mode = resolveCollabAccessMode(userProfile.role);

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

  return { careNote: row, mode };
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
 * Insert a point-in-time snapshot into `note_versions`.
 *
 * Delegates numbering to the create_note_version() SQL function, which takes a
 * per-care-note advisory lock and allocates the version inside one transaction.
 * This code used to read MAX(version_number), add one, and insert -- a
 * read-then-write that collides whenever two flushes land together. Under the
 * 3s collaborative debounce that is the normal case, and the failure was caught
 * and logged rather than thrown, so version snapshots simply stopped appearing.
 *
 * `changedBy` is nullable on purpose. It is a uuid foreign key, so a
 * system-authored snapshot passes null -- never a sentinel string like
 * "system", which fails the type parse and loses the snapshot.
 */
export async function createNoteVersion(
  careNoteId: string,
  yjsSnapshot: Uint8Array,
  changedBy: string | null,
  contentSnapshot: Record<string, unknown> | null = null,
  changeSummary: string = "Auto-saved version"
): Promise<number> {
  const supabase = getSupabaseAdmin();

  const { data, error } = await supabase.rpc("create_note_version", {
    p_care_note_id: careNoteId,
    p_changed_by: changedBy,
    p_content_snapshot: contentSnapshot,
    p_change_summary: changeSummary,
    p_yjs_snapshot: Buffer.from(yjsSnapshot).toString("base64"),
  });

  if (error) {
    console.error(
      `[persistence] Failed to create version for ${careNoteId}:`,
      error.message
    );
    throw error;
  }

  const version = typeof data === "number" ? data : Number(data);
  console.log(
    `[persistence] Created version ${version} for ${careNoteId} by ${changedBy ?? "system"}`
  );
  return version;
}
