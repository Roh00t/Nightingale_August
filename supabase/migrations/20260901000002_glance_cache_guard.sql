-- Stop the clinical assessment being written back into the patient-readable
-- column, whatever the client does.
--
-- HOW THE LEAK CAME BACK.
--
-- The assessment lives in `care_note_assessments`, which has no patient policy.
-- To avoid rewriting every downstream component, `/patients/[id]` fetches it for
-- care-team viewers and recomposes it into `glance_cache` **in memory** before
-- rendering. That was intended as a display-only convenience.
--
-- It is not display-only, because the same object is a write source. Both
-- browser writes to the care plan spread it:
--
--     .update({ glance_cache: { ...careNote.glance_cache, care_plan_items } })
--
-- So a clinician opening a note and ticking a care-plan item silently persisted
-- the assessment back into the column a patient can read. Nothing failed, no
-- test caught it, and the row looked exactly as it had before the original fix.
--
-- Application-side stripping is not sufficient: it has to be remembered at every
-- write site, and it was the *absence* of that memory that caused this. So the
-- guarantee moves to where it cannot be forgotten. A patient-facing column is
-- now incapable of holding these keys.
--
-- Idempotent.

BEGIN;

CREATE OR REPLACE FUNCTION strip_internal_glance_keys()
RETURNS trigger AS $$
BEGIN
  -- `- key` on jsonb is a no-op when the key is absent, so this costs nothing on
  -- the overwhelming majority of writes that never carried them.
  NEW.glance_cache := COALESCE(NEW.glance_cache, '{}'::jsonb)
                      - 'top_items'
                      - 'changes_since_last_visit';
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_strip_internal_glance_keys ON care_notes;

-- BEFORE, so the row is corrected rather than rejected. Raising here would break
-- an ordinary care-plan tick for a clinician who did nothing wrong — the client
-- is sending a superset it did not mean to send, not attempting an attack.
CREATE TRIGGER trg_strip_internal_glance_keys
  BEFORE INSERT OR UPDATE ON care_notes
  FOR EACH ROW EXECUTE FUNCTION strip_internal_glance_keys();

-- Repair anything already written back. This is the row the live probe found.
UPDATE care_notes
SET glance_cache = glance_cache - 'top_items' - 'changes_since_last_visit'
WHERE glance_cache ? 'top_items'
   OR glance_cache ? 'changes_since_last_visit';

COMMIT;
