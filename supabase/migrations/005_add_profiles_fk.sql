-- Add a foreign key from timeline_entries.author_id to profiles.id
-- so PostgREST can resolve joins like: select=*,author:profiles(*)
-- The original FK points to auth.users which is not directly joinable to profiles.

ALTER TABLE timeline_entries
  ADD CONSTRAINT timeline_entries_author_profile_fkey
  FOREIGN KEY (author_id) REFERENCES profiles(id);

-- Also add the same for comments.author_id if it references auth.users
ALTER TABLE comments
  ADD CONSTRAINT comments_author_profile_fkey
  FOREIGN KEY (author_id) REFERENCES profiles(id);
