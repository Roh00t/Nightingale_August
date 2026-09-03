-- Telegram as a delivery channel and a passwordless identity path.
--
-- THE CONSTRAINT THAT SHAPES THIS.
--
-- The Telegram Bot API **cannot message a phone number**. A bot may only send to
-- a `chat_id`, and a chat_id exists only after the person has opened a
-- conversation with the bot themselves. There is no API to initiate contact.
--
-- That is not a limitation to work around — it is the consent model, and it is
-- why the flow is a deep link rather than an outbound send:
--
--   1. Front desk generates a token and shows/sends `t.me/<Bot>?start=<token>`.
--   2. The patient taps it and presses Start. Telegram delivers `/start <token>`
--      to our webhook **with** their chat_id.
--   3. We bind that chat_id to the profile the token belongs to, and only then
--      can the clinic message them.
--
-- So a patient with no email and only a phone number is reachable — but only
-- after one deliberate tap. Any design that claims otherwise is describing
-- something the platform does not do.
--
-- Idempotent.

BEGIN;

-- Telegram's chat_id is a signed 64-bit integer, and for channels it can exceed
-- int4. Stored as bigint rather than text so an ordering or equality bug cannot
-- silently match the wrong chat.
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS telegram_chat_id bigint;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS telegram_linked_at timestamptz;

-- One chat per profile per clinic, and one profile per chat. Without this a
-- shared family handset could end up bound to two patients, and a message
-- intended for one would reach the other.
CREATE UNIQUE INDEX IF NOT EXISTS idx_profiles_telegram_chat
  ON profiles(telegram_chat_id) WHERE telegram_chat_id IS NOT NULL;

-- The delivery row records which chat actually received it, kept separate from
-- profiles.telegram_chat_id so a later re-link does not rewrite history: a
-- failure has to be diagnosable against the chat that was actually used.
ALTER TABLE message_deliveries ADD COLUMN IF NOT EXISTS telegram_chat_id bigint;

-- Telegram acknowledges with its own message_id; provider_message_id already
-- exists and carries it.

COMMENT ON COLUMN profiles.telegram_chat_id IS
  'Bound only after the patient opens the bot via a t.me deep link. Cannot be derived from a phone number.';

COMMIT;
