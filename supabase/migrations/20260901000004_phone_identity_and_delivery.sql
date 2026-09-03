-- Phone-first patient identity, and delivery tracing for the links we send.
--
-- THE PROBLEM WITH EMAIL.
--
-- `profiles` currently descends from `auth.users`, which requires an email.
-- For a Singapore community clinic that is the wrong assumption: an elderly
-- patient reachable on WhatsApp very often has no email address they can
-- retrieve, and staff work around it by inventing one — patient1@clinic.local —
-- which then becomes an identifier nobody can receive anything at. The account
-- exists, the patient cannot log in, and the clinic believes they can.
--
-- This migration adds the phone as a first-class identity and a token-link path
-- that does not require the patient to have or remember credentials at all.
--
-- Idempotent.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Phone as an identifier on the profile
-- ---------------------------------------------------------------------------
-- E.164, enforced by CHECK. Storing "9123 4567" and "+6591234567" as different
-- strings for the same person is how a patient ends up with two records and
-- half a history in each.
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS phone_e164 text;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS phone_verified_at timestamptz;

DO $$ BEGIN
  ALTER TABLE profiles ADD CONSTRAINT profiles_phone_e164_format
    CHECK (phone_e164 IS NULL OR phone_e164 ~ '^\+[1-9]\d{7,14}$');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Unique per clinic, not globally: the same household phone legitimately
-- reaches a patient at one clinic and a caregiver at another, and a global
-- unique constraint would make the second registration fail with a confusing
-- error at the front desk.
CREATE UNIQUE INDEX IF NOT EXISTS idx_profiles_phone_clinic
  ON profiles(clinic_id, phone_e164) WHERE phone_e164 IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 2. Access tokens — the no-email, no-password path
-- ---------------------------------------------------------------------------
-- A patient receives a link on WhatsApp/SMS and follows it. No account recall
-- required, which is the point.
--
-- Only a HASH of the token is stored. The table is readable by staff for
-- support purposes, and a plaintext token in a support view is a credential
-- lying in the open — anyone who can read the row can impersonate the patient.
-- The plaintext exists once, in the message that was sent.
CREATE TABLE IF NOT EXISTS patient_access_tokens (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id    uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  clinic_id     uuid NOT NULL REFERENCES clinics(id),
  token_hash    text NOT NULL UNIQUE,
  purpose       text NOT NULL DEFAULT 'portal_access'
                CHECK (purpose IN ('portal_access', 'appointment', 'otp')),
  -- Short by default. A link that reaches the wrong phone — a recycled number,
  -- a shared family handset — is a standing exposure for as long as it is valid.
  expires_at    timestamptz NOT NULL DEFAULT (now() + interval '72 hours'),
  -- Single-use for OTP; reusable within the window for portal links.
  consumed_at   timestamptz,
  max_uses      integer NOT NULL DEFAULT 1 CHECK (max_uses >= 1),
  use_count     integer NOT NULL DEFAULT 0,
  -- Throttling. Without this a token is brute-forceable at whatever rate the
  -- attacker can issue requests.
  failed_attempts integer NOT NULL DEFAULT 0,
  created_by    uuid REFERENCES profiles(id),
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tokens_profile ON patient_access_tokens(profile_id, expires_at);

ALTER TABLE patient_access_tokens ENABLE ROW LEVEL SECURITY;

-- Care team only, and clinic-scoped. Patients cannot list tokens — including
-- their own: enumerating live tokens is useful to an attacker and to nobody else.
DROP POLICY IF EXISTS "Care team manages access tokens" ON patient_access_tokens;
CREATE POLICY "Care team manages access tokens"
  ON patient_access_tokens FOR ALL
  USING (
    clinic_id = get_user_clinic_id()
    AND get_user_role() IN ('staff', 'clinician', 'admin')
  );

-- ---------------------------------------------------------------------------
-- 3. Delivery tracing
-- ---------------------------------------------------------------------------
-- Generating a link is not the same event as the patient receiving it, and the
-- gap between them is where the clinical assumption breaks: staff see "link
-- sent", believe the patient has their appointment details, and the patient
-- never got the message because the number was wrong, the handset was off, or
-- the provider silently dropped it.
--
-- `status` starts at 'queued' and only advances on provider webhook. There is
-- deliberately no 'sent' default: the whole point is that our side of the
-- handoff proves nothing about receipt.
CREATE TABLE IF NOT EXISTS message_deliveries (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  clinic_id      uuid NOT NULL REFERENCES clinics(id),
  profile_id     uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  care_note_id   uuid REFERENCES care_notes(id) ON DELETE SET NULL,
  -- Which timeline entry this message corresponds to, when it was a patient
  -- message rather than a bare appointment link.
  entry_id       uuid REFERENCES timeline_entries(id) ON DELETE SET NULL,
  token_id       uuid REFERENCES patient_access_tokens(id) ON DELETE SET NULL,
  channel        text NOT NULL CHECK (channel IN ('whatsapp', 'sms', 'email')),
  -- The address as dialled, kept so a failure can be diagnosed against the
  -- number actually used rather than the number currently on the profile.
  destination    text NOT NULL,
  provider       text,
  provider_message_id text,
  status         text NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued','sent','delivered','read','failed','undeliverable')),
  -- Provider's reason, verbatim. "failed" alone does not tell a receptionist
  -- whether to re-send or to correct the number.
  failure_reason text,
  attempts       integer NOT NULL DEFAULT 0,
  queued_at      timestamptz NOT NULL DEFAULT now(),
  sent_at        timestamptz,
  delivered_at   timestamptz,
  failed_at      timestamptz,
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_deliveries_profile ON message_deliveries(clinic_id, profile_id, queued_at DESC);
-- Partial index: the query staff actually run is "what has not arrived", and it
-- should stay fast as the delivered rows accumulate.
CREATE INDEX IF NOT EXISTS idx_deliveries_unresolved
  ON message_deliveries(clinic_id, status) WHERE status IN ('queued','sent','failed');

ALTER TABLE message_deliveries ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Care team views deliveries" ON message_deliveries;
CREATE POLICY "Care team views deliveries"
  ON message_deliveries FOR SELECT
  USING (
    clinic_id = get_user_clinic_id()
    AND get_user_role() IN ('staff', 'clinician', 'admin')
  );

-- No INSERT/UPDATE policy for user roles by design. Delivery status is asserted
-- by the provider webhook through the service role; a status a clinician can
-- type in is not evidence of anything.

-- ---------------------------------------------------------------------------
-- 4. Retraction (Audit 12)
-- ---------------------------------------------------------------------------
-- A patient message that was wrong cannot be deleted — the patient has already
-- read it, and a vanished message is worse than a corrected one. It is marked,
-- and the correction is a new event on the timeline.
ALTER TABLE timeline_entries ADD COLUMN IF NOT EXISTS is_retracted boolean NOT NULL DEFAULT false;
ALTER TABLE timeline_entries ADD COLUMN IF NOT EXISTS retracted_at timestamptz;
ALTER TABLE timeline_entries ADD COLUMN IF NOT EXISTS retracted_by uuid REFERENCES profiles(id);
ALTER TABLE timeline_entries ADD COLUMN IF NOT EXISTS retraction_reason text;

COMMIT;
