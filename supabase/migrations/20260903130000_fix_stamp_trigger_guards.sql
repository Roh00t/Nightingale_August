-- Both stamping triggers were inert. Found while testing ui_telemetry.
--
-- =============================================================================
-- THE BUG
-- =============================================================================
-- Both functions opened with:
--
--     IF current_user NOT IN ('authenticated', 'anon') THEN RETURN NEW; END IF;
--
-- intending "if this is a service-role or owner call, leave the row alone".
-- But both are SECURITY DEFINER, and inside a SECURITY DEFINER function
-- `current_user` is the FUNCTION OWNER, not the caller. So the condition was
-- always true, the early return always taken, and neither trigger ever stamped
-- anything.
--
-- Demonstrated on a seeded local database, as an authenticated clinician:
--
--     INSERT INTO interaction_log (user_id, user_role, action_type, ...,
--       target_metadata)
--     VALUES (auth.uid(), 'admin', 'accept', ...,
--       '{"keywords":["x"],"secret_note":"Patient Alice Wong has HIV"}');
--
--     -> stored_role:     admin          (a role the caller does not hold)
--     -> stored_metadata: kept verbatim, including the free-text PHI
--
-- Two consequences on interaction_log:
--   1. The metadata allowlist that 20260902000002_rls_hardening.sql documents as
--      "server-side authority over what the learning loop reads" stripped
--      nothing. Arbitrary keys — including free text, including PHI — persisted
--      in a column the PHI posture treats as metadata-only.
--   2. user_role was caller-supplied, so the self-learning loop's view of who
--      did what was forgeable by its own subjects.
--
-- NOT AFFECTED: pin_profile_identity_columns() uses the same guard but is NOT
-- SECURITY DEFINER, so its `current_user` really is the caller and the
-- privilege-escalation fix works as documented. Verified, not assumed.
--
-- =============================================================================
-- THE FIX
-- =============================================================================
-- Guard on `auth.uid() IS NULL` instead. It answers the question actually being
-- asked — "is there an end-user JWT behind this call?" — and it is unaffected by
-- SECURITY DEFINER, because it reads the request GUC rather than the executing
-- role. Service-role and seed inserts have no JWT, so they still pass through
-- untouched, which is what the original guard was for.

CREATE OR REPLACE FUNCTION stamp_interaction_log()
RETURNS trigger AS $$
DECLARE
  v_role text;
BEGIN
  -- No end-user JWT => service role, seed, or migration. Leave the row as given.
  -- Deliberately NOT `current_user`: this function is SECURITY DEFINER, so
  -- current_user is the owner and the check would always pass.
  IF auth.uid() IS NULL THEN
    RETURN NEW;
  END IF;
  NEW.user_id    := auth.uid();
  NEW.created_at := now();
  SELECT role INTO v_role FROM public.profiles WHERE id = auth.uid();
  NEW.user_role := COALESCE(v_role, 'unknown');
  IF NEW.target_metadata IS NOT NULL THEN
    NEW.target_metadata := jsonb_strip_nulls(jsonb_build_object(
      'keywords',   NEW.target_metadata -> 'keywords',
      'topic',      NEW.target_metadata -> 'topic',
      'risk_level', NEW.target_metadata -> 'risk_level'
    ));
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;

CREATE OR REPLACE FUNCTION stamp_ui_telemetry()
RETURNS trigger AS $$
DECLARE
  v_role   text;
  v_clinic uuid;
BEGIN
  IF auth.uid() IS NULL THEN
    RETURN NEW;
  END IF;
  SELECT role, clinic_id INTO v_role, v_clinic
    FROM public.profiles WHERE id = auth.uid();
  NEW.user_role  := COALESCE(v_role, 'unknown');
  NEW.clinic_id  := v_clinic;
  NEW.created_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public;
