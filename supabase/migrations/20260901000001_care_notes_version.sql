-- Optimistic concurrency control on care_notes, and addressable highlight
-- provenance.
--
-- WHERE THE CONFLICT ACTUALLY IS.
--
-- When the Hocuspocus collab server is reachable, concurrent editing is handled
-- by Yjs: the CRDT merges character-level operations and there is no lost
-- update to protect against. OCC there would be wrong — it would reject merges
-- the CRDT resolves correctly.
--
-- The window is when collab is NOT reachable. The editor falls back to "Local
-- Only" mode (frontend/components/editor/CareNoteEditor.tsx) and writes
-- `yjs_state` straight to Supabase with a plain UPDATE. Two clinicians with the
-- note open — the exact situation in a clinic where the collab process died —
-- both write, and the second silently erases the first. No error, no version,
-- no trace: the note simply loses an examination finding.
--
-- That is what this version column guards. It is deliberately NOT a general
-- lock on all writes to the row.
--
-- Idempotent.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. version column
-- ---------------------------------------------------------------------------

ALTER TABLE care_notes ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1;

-- ---------------------------------------------------------------------------
-- 2. Compare-and-swap save
-- ---------------------------------------------------------------------------
-- Returns the new version on success, NULL when the caller's version was stale.
--
-- NULL rather than an exception, because a stale save is a normal event in a
-- two-clinician clinic, not an error condition — the caller needs to show a
-- merge prompt, and an exception would be indistinguishable from the database
-- being down. The caller must treat NULL as "someone else saved; do not
-- overwrite" and surface it. Silently retrying with the fresh version would
-- reintroduce exactly the clobber this exists to prevent.
--
-- SECURITY INVOKER (the default): this runs as the caller, so RLS still decides
-- whether they may touch the row. A SECURITY DEFINER version would let anyone
-- who can call the function write any clinic's note.
CREATE OR REPLACE FUNCTION save_care_note_yjs(
  p_care_note_id    uuid,
  p_yjs_state       text,      -- base64; PostgREST renders bytea this way
  p_expected_version integer
)
RETURNS integer AS $$
DECLARE
  v_new_version integer;
BEGIN
  UPDATE public.care_notes
     SET yjs_state  = decode(p_yjs_state, 'base64'),
         version    = version + 1,
         updated_at = now()
   WHERE id = p_care_note_id
     AND version = p_expected_version
  RETURNING version INTO v_new_version;

  -- v_new_version is NULL when no row matched: either the id is invisible to
  -- this caller under RLS, or the version moved on. Both mean "do not write".
  RETURN v_new_version;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 3. Addressable highlight provenance
-- ---------------------------------------------------------------------------
-- A highlight points at a source entry. Entries get edited, so "click through
-- to the source" can land on text that no longer contains the quoted claim —
-- and it does so silently, which is the part that matters: the clinician sees a
-- highlight, follows it, reads different text, and has no signal that the
-- ground moved. They conclude the highlight was wrong, or worse, that the new
-- text supports it.
--
-- Storing the version and a hash of the exact quote makes staleness detectable
-- rather than invisible. The hash is of the quote only, not the whole entry: an
-- unrelated edit elsewhere in a long note should not invalidate a highlight
-- whose supporting sentence is untouched.

ALTER TABLE highlights ADD COLUMN IF NOT EXISTS source_note_version integer;
ALTER TABLE highlights ADD COLUMN IF NOT EXISTS exact_quote_hash    text;

COMMENT ON COLUMN highlights.source_note_version IS
  'care_notes.version at extraction time. Compared against current to detect drift.';
COMMENT ON COLUMN highlights.exact_quote_hash IS
  'sha256 of the normalised supporting quote. Mismatch means the source text changed.';

-- ---------------------------------------------------------------------------
-- 4. Staleness, computed rather than stored
-- ---------------------------------------------------------------------------
-- A stored `is_stale` flag would need every entry edit to remember to update
-- every highlight derived from it, and the failure mode of forgetting is a
-- highlight that claims to be current when it is not. Deriving it on read
-- cannot go stale by omission.
CREATE OR REPLACE FUNCTION highlight_source_changed(p_highlight_id uuid)
RETURNS boolean AS $$
  SELECT CASE
    -- No recorded version: extracted before provenance tracking existed. Report
    -- unknown-as-changed so the UI degrades to "Source Modified" rather than
    -- asserting freshness it cannot support.
    WHEN h.source_note_version IS NULL THEN true
    ELSE h.source_note_version <> c.version
  END
  FROM public.highlights h
  JOIN public.care_notes c ON c.id = h.care_note_id
  WHERE h.id = p_highlight_id;
$$ LANGUAGE sql STABLE;

COMMIT;
