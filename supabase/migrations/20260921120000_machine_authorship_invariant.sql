-- Machine-authored entries must be machine-authored, and must say where they
-- came from.
--
-- =============================================================================
-- WHAT THIS CLOSES
-- =============================================================================
-- The "Generate AI Summary" button wrote its own timeline row from the browser:
--
--     entry_type: 'ai_doctor_consult_summary'
--     author_role: 'system'
--     author_id:  <the signed-in clinician>     -- a human, on a machine row
--     provenance_pointer: (absent)
--
-- The database accepted it. RLS on timeline_entries only requires
-- author_id = auth.uid() for a user-JWT insert, and nothing anywhere said that
-- author_role 'system' means author_id IS NULL — that invariant existed only in
-- a docstring on insert_system_timeline_entry and in the shape of the seed.
--
-- Two things were wrong with the resulting row. It attributed machine-generated
-- text to a named clinician, which is the opposite of the separation the whole
-- provenance model exists to maintain. And it carried no provenance_pointer, so
-- there was nothing tying the summary to the run that produced it.
--
-- The browser could not have written a correct row: the correct shape is
-- author_role 'system' with author_id NULL, and no INSERT policy admits that —
-- deliberately, since a session that could write it could forge an entry
-- attributed to the AI scribe. So the write moved to the service, behind the
-- service-role key, and these constraints make the old shape impossible rather
-- than merely unused.

-- 1. A system-authored row has no human author.
ALTER TABLE timeline_entries
  DROP CONSTRAINT IF EXISTS timeline_entries_system_has_no_author;
ALTER TABLE timeline_entries
  ADD CONSTRAINT timeline_entries_system_has_no_author
  CHECK (author_role <> 'system' OR author_id IS NULL);

-- 2. AI-generated text says what produced it.
--
-- Scoped to ai_* entry types, not to author_role='system'. system_event rows
-- (lab feeds, archival notices) are machine-written but are not model output
-- and have nothing to point provenance at; requiring it there would be
-- ceremony, and a constraint that has to be worked around is one that gets
-- dropped.
ALTER TABLE timeline_entries
  DROP CONSTRAINT IF EXISTS timeline_entries_ai_has_provenance;
ALTER TABLE timeline_entries
  ADD CONSTRAINT timeline_entries_ai_has_provenance
  CHECK (entry_type NOT LIKE 'ai\_%' OR provenance_pointer IS NOT NULL);
