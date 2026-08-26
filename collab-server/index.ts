import { config } from "dotenv";
config({ path: "../.env" });
import { Hocuspocus } from "@hocuspocus/server";
import * as Y from "yjs";
import {
  authenticateUser,
  type UserProfile,
} from "./auth.js";
import {
  parseCareNoteId,
  verifyCareNoteClinicScope,
  loadYjsState,
  saveYjsState,
  createNoteVersion,
} from "./persistence.js";

// ----------------------------------------------------------------
// Debounce tracking for version snapshots
// ----------------------------------------------------------------

/**
 * Tracks the last time a version snapshot was created per document,
 * so we create at most one snapshot every VERSION_DEBOUNCE_MS.
 */
const lastVersionSnapshot = new Map<string, number>();
const VERSION_DEBOUNCE_MS = 30_000; // 30 seconds

// ----------------------------------------------------------------
// Extended context attached to each connection
// ----------------------------------------------------------------

interface ConnectionContext {
  user: UserProfile;
  careNoteId: string;
}

// ----------------------------------------------------------------
// Server configuration
// ----------------------------------------------------------------

const port = parseInt(process.env.HOCUSPOCUS_PORT || "1234", 10);

const server = new Hocuspocus({
  name: "nightingale-collab",
  port,
  /**
   * The debounce interval (in ms) for the onStoreDocument hook.
   * Hocuspocus will wait this long after the last change before
   * calling onStoreDocument, preventing excessive writes.
   */
  debounce: 3000,
  /**
   * Maximum time (in ms) that changes can remain un-stored.
   * Even if edits keep streaming in, the document will be
   * persisted at least once within this window.
   */
  maxDebounce: 10_000,

  // ------------------------------------------------------------------
  // Lifecycle hooks
  // ------------------------------------------------------------------

  async onAuthenticate(data) {
    const { token, documentName } = data;

    // 1. Verify JWT and fetch user profile
    if (!token) {
      throw new Error("Authentication token is required");
    }

    const user = await authenticateUser(token);

    // 2. Parse the care note ID from the document name
    const careNoteId = parseCareNoteId(documentName);

    // 3. Verify clinic scope -- user must belong to same clinic
    await verifyCareNoteClinicScope(careNoteId, user);

    console.log(
      `[auth] User ${user.display_name} (${user.role}) authenticated for ${documentName}`
    );

    // 4. Return context that will be available in subsequent hooks
    const context: ConnectionContext = { user, careNoteId };
    return context;
  },

  async onLoadDocument(data) {
    const { documentName, document } = data;

    const careNoteId = parseCareNoteId(documentName);

    // Load persisted Yjs state from Supabase
    const existingState = await loadYjsState(careNoteId);

    if (existingState && existingState.length > 0) {
      // Apply the stored Yjs update to the Hocuspocus Y.Doc
      try {
        Y.applyUpdate(document, existingState);
        console.log(
          `[persistence] Loaded ${existingState.length} bytes of Yjs state for ${documentName}`
        );
      } catch (err) {
        console.warn(
          `[persistence] Corrupted Yjs state for ${documentName}, starting fresh:`,
          err instanceof Error ? err.message : err
        );
        // Clear the corrupted state from the database so it doesn't
        // keep failing on every subsequent connection
        try {
          await saveYjsState(careNoteId, Y.encodeStateAsUpdate(document));
          console.log(
            `[persistence] Cleared corrupted state for ${documentName}`
          );
        } catch (clearErr) {
          console.error(
            `[persistence] Failed to clear corrupted state for ${documentName}:`,
            clearErr
          );
        }
      }
    } else {
      console.log(
        `[persistence] No existing Yjs state for ${documentName}, starting fresh`
      );
    }

    return document;
  },

  async onStoreDocument(data) {
    const { documentName, document, context } = data;

    const careNoteId = parseCareNoteId(documentName);

    // Encode the full Yjs document state
    const state = Y.encodeStateAsUpdate(document);

    // 1. Save the Yjs state to care_notes
    await saveYjsState(careNoteId, state);

    // 2. Create a version snapshot (debounced to every 30 seconds)
    const now = Date.now();
    const lastSnapshot = lastVersionSnapshot.get(careNoteId) ?? 0;

    if (now - lastSnapshot >= VERSION_DEBOUNCE_MS) {
      lastVersionSnapshot.set(careNoteId, now);

      // Extract the user who last changed from context.
      // onStoreDocument's context is a map of all connected users'
      // contexts; we pick the first available user as the "changed_by".
      const connContext = resolveChangedByUser(context);
      const changedByUserId = connContext?.user.id ?? "system";

      // Build a lightweight JSON content snapshot from the Y.Doc
      // so humans can browse version diffs without decoding Yjs binary.
      const contentSnapshot = extractContentSnapshot(document);

      try {
        await createNoteVersion(
          careNoteId,
          state,
          changedByUserId,
          contentSnapshot,
          "Auto-saved collaborative edit"
        );
      } catch (err) {
        // Version creation failure should not block persistence
        console.error(
          `[persistence] Version snapshot failed for ${careNoteId}:`,
          err
        );
      }
    }
  },

  async onDisconnect(data) {
    const { documentName, document, clientsCount } = data;

    // When the last client disconnects, ensure final state is saved
    if (clientsCount === 0) {
      const careNoteId = parseCareNoteId(documentName);
      const state = Y.encodeStateAsUpdate(document);
      await saveYjsState(careNoteId, state);
      console.log(
        `[persistence] Final save on last disconnect for ${documentName}`
      );

      // Clean up debounce tracking
      lastVersionSnapshot.delete(careNoteId);
    }
  },
});

// ----------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------

/**
 * Resolve a single user from the onStoreDocument context.
 *
 * Hocuspocus v2 passes the context from onAuthenticate as the
 * `context` field on onStoreDocument. When multiple clients are
 * connected, the context corresponds to one of the connected users.
 */
function resolveChangedByUser(
  context: Record<string, unknown> | unknown
): ConnectionContext | null {
  if (!context || typeof context !== "object") return null;

  // Hocuspocus attaches the return value of onAuthenticate as context
  const ctx = context as Partial<ConnectionContext>;
  if (ctx.user && ctx.careNoteId) {
    return ctx as ConnectionContext;
  }

  return null;
}

/**
 * Extract a human-readable JSON snapshot from a Y.Doc.
 *
 * Iterates over all shared types in the document and converts them
 * to plain JSON. This is used for the `content_snapshot` column in
 * `note_versions` so reviewers can see diffs without Yjs tooling.
 */
function extractContentSnapshot(
  doc: InstanceType<typeof import("yjs").Doc>
): Record<string, unknown> {
  const snapshot: Record<string, unknown> = {};

  try {
    // Y.Doc.share is a Map of shared type name -> AbstractType
    doc.share.forEach((sharedType, key) => {
      // XmlFragment (used by TipTap/ProseMirror) can be converted to JSON
      if ("toJSON" in sharedType && typeof sharedType.toJSON === "function") {
        snapshot[key] = sharedType.toJSON();
      }
    });
  } catch (err) {
    console.warn("[persistence] Failed to extract content snapshot:", err);
    snapshot["_error"] = "Failed to extract content";
  }

  return snapshot;
}

// ----------------------------------------------------------------
// Start server
// ----------------------------------------------------------------

server.listen().then(() => {
  console.log(
    `[nightingale-collab] Hocuspocus server running on port ${port}`
  );
  console.log(
    `[nightingale-collab] WebSocket endpoint: ws://localhost:${port}`
  );
  console.log(
    `[nightingale-collab] Document format: care-note:{care_note_id}`
  );
});
