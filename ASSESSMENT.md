# Nightingale — Clinical Resilience Self-Assessment

Sixteen failure modes a real clinic produces, and what this system actually does
when it meets them. Written against the code as it stands on `master`, verified
by running it rather than by reading it.

**Verification basis:** 420 tests pass (`cd ai-service && .venv/bin/python -m pytest tests/ -q`);
`tsc --noEmit` clean on both TypeScript projects; `next build` compiles; all three
migrations applied to a throwaway PostgreSQL cluster built from
`supabase/migrations/001_foundation.sql`, then re-applied to confirm idempotency.

Status has three values and they are used strictly:

- **SURVIVES** — the failure is handled and there is a test or a live probe that
  fails if the handling is removed.
- **PARTIAL** — the mechanism exists and works, but a real gap remains, named below.
- **DOES NOT** — the schema or scaffolding may exist; the behaviour does not.

Six items are PARTIAL and one DOES NOT. That distribution is the honest result,
not a rounding of eleven successes.

---

### 1. Identity & WhatsApp/Phone Auth

- **Status:** PARTIAL
- **Where:** `supabase/migrations/20260901_phone_identity_and_delivery.sql:20-90`
  adds `profiles.phone_e164` (E.164 `CHECK`), `phone_verified_at`, a per-clinic
  unique index, and the `patient_access_tokens` table (hash-only storage, 72h
  expiry, use counting, failed-attempt column). **No application code reads or
  writes any of it** — there is no OTP send, no token redemption route, no
  session exchange. `frontend/lib/auth.ts` does not exist; auth is
  `@supabase/ssr` email+password throughout.
- **What Breaks First:** The front desk. A patient with no email still cannot be
  registered, because `auth.users` requires one and nothing here changes that —
  staff will keep inventing `patient1@clinic.local`, which is the exact failure
  the audit names. Nothing catches it: the invented address is a valid string and
  the account is created successfully. The schema is a foundation, and calling
  this anything better than PARTIAL would be false — the storage for a credential
  is not a credential flow.

### 2. Row-Level Security & Multi-Clinic Isolation

- **Status:** SURVIVES
- **Where:** `supabase/migrations/20260901_multi_clinic_rls.sql`. Denormalised
  `clinic_id` on `timeline_entries`, `note_versions`, `comments`, `highlights`,
  `care_note_assessments` (lines 44-64); `set_clinic_id_from_care_note()` trigger
  (lines 76-110); `RESTRICTIVE` tenant policies (lines 125-140).
- **Deliberate deviation.** The audit specified
  `auth.jwt() ->> 'clinic_id' = clinic_id`. This does not do that, for two
  reasons stated at lines 5-25 of the migration. Supabase access tokens do not
  carry `clinic_id` without a custom access-token hook — absent the claim the
  comparison is `NULL`, every policy denies, and the whole application locks out;
  the predictable "fix" is a `COALESCE` that silently removes the isolation
  entirely. And a JWT claim is a snapshot: move a clinician between clinics and
  their existing token keeps reading the old one until it expires. Scoping stays
  on `get_user_clinic_id()`, a `SECURITY DEFINER` read of `profiles`, which is
  authoritative immediately. The property the audit is *reaching for* — isolation
  that holds even if a handler forgets to filter — is delivered by the
  denormalised column and the restrictive policy instead.
- **What Breaks First:** Nothing observed. Probed live on a seeded cluster:
  clinician A reading clinic B's entries returns **0 rows**; reading their own
  returns 8. An INSERT supplying a *foreign* `clinic_id` has it overwritten by
  the trigger — a caller-settable denormalised column would be worse than none,
  because it looks like an independent check while being attacker-controlled.
  The policies are `RESTRICTIVE`, so they AND with existing ones; adding them
  `PERMISSIVE` would have *widened* access, which is the trap here.
  The remaining exposure is the service-role key, which bypasses RLS by design —
  three call sites re-implement the checks by hand (`guardrails.md` S3).

### 3. Log Scrubbing & Privacy Middleware

- **Status:** PARTIAL
- **Where:** `ai-service/services/log_scrubbing.py` — `PHIAnonymizingLogFilter`
  (line 97), `PHIScrubbingFormatter` (line 128), `install()` (line 145). Attached
  to the **root** logger at import time in `ai-service/main.py:71`, before the app
  is constructed, so uvicorn access/error records and third-party tracebacks are
  covered, not just `nightingale.*`.
- **What Breaks First:** A free-text name in a log line. Verified live:
  `GET /x?nric=S1234567D` becomes `<NRIC_REDACTED>`, `patient: Alice Wong`
  becomes `<NAME_REDACTED>` — but `"Patient Alice Wong called"`, with no `:` or
  `=` separator, **passes through unscrubbed**. That is a deliberate limit, not
  an oversight: names are only scrubbed in labelled positions, because a
  bare capitalised-word rule eats "Lisinopril" and "Monday" and makes logs
  useless, which gets the filter switched off. NRIC, phone and email are
  structural and are caught wherever they appear. What is guaranteed is narrower
  than "PHI never reaches logs" and is stated as such at the top of the module.
  Presidio is deliberately not used here — model inference on the emitting thread
  would add latency to every record and can deadlock if the analyser itself logs.

### 4. Verifiable Redaction Call Path

- **Status:** SURVIVES
- **Where:** `ai-service/services/egress_guard.py` — `assert_safe_for_model()`
  (line 100), `RedactedText` (line 66). Called at
  `ai-service/services/llm.py:95`, inside `_call_with_retry`, which is the
  **single** point every Groq call funnels through (`client.chat.completions.create`
  appears exactly once in the codebase).
- **What Breaks First:** Nothing silently. Verified live: a prompt containing
  `S1234567D` raises `UnredactedEgressError` **before** the network call.
  The design point is that ordering was previously enforced only by code reading
  — `redact()` on one line, the model call twenty lines later — and code reading
  does not survive refactoring. A new endpoint or an added `patient_context=`
  argument reintroduces the leak with no test failing, because existing tests
  assert the *redactor's* behaviour, not that every prompt passed through it.
  It refuses rather than repairs: silently fixing one leaked field would hide the
  call path that skipped redaction. `RedactedText` is a `str` subclass and is
  **honest about carrying no runtime power** — it is a label for type signatures
  and review; the enforcement is the scan, which trusts nothing. It catches
  structured identifiers only (NRIC, SG phone) and cannot detect an un-redacted
  free-text name; that remains Presidio's job upstream.

### 5. Code-Switching SEA Audio Transcript Prompting

- **Status:** PARTIAL
- **Where:** `ai-service/services/transcription.py:249-266` (rationale for
  omitting `language_code`); `ai-service/services/llm.py:45` —
  `CODE_SWITCHING_GUIDANCE`, injected into the summary system prompt at line 176.
- **The audit's premise does not hold here.** This service uses **ElevenLabs
  Scribe v2**, not Whisper (`MODEL_ID = "scribe_v2"`). Scribe has no Whisper-style
  free-text `prompt` parameter, so there is no way to feed the ASR a
  Malay/Hokkien clinical glossary at the audio layer. What is available is
  (a) *not* pinning `language_code`, which matters more than it sounds — an
  English prior decodes "gula darah" into plausible English nonsense rather than
  leaving an obvious gap a clinician would catch — and (b) instructing the
  downstream summariser, which is a prompt this codebase does control.
- **What Breaks First:** A Hokkien term the summariser does not recognise. The
  prompt requires it be reproduced verbatim and marked `[unclear]` rather than
  dropped, because a dropped term is a silent loss of clinical content and an
  `[unclear]` one is a question a clinician can answer. **This is untested against
  real code-switched audio** — the `elevenlabs` package is not installed and live
  transcription is off by default, so the mock path is what runs. The instruction
  is plausible and unverified, which is why this is PARTIAL.

### 6. Streaming & Timeout Guardrails

- **Status:** SURVIVES
- **Where:** `frontend/lib/ai_client.ts` — `callAI()` (line 51), `AI_TIMEOUT_MS`
  25s (line 17), `AIServiceError` with a `kind` discriminant (line 21).
  `frontend/components/ui/AITimeoutFallback.tsx:20`. All four AI calls in
  `frontend/components/patient/PatientWorkspace.tsx` now route through it (`grep -c "await fetch("` returns
  **0** in that file). `frontend/components/voice/VoiceCapture.tsx:157-186` has its own 120s bound.
- **What Breaks First:** Nothing hangs. Previously `fetch` had no timeout at all,
  so a stalled FastAPI process left the promise pending forever and the
  clinician's only recourse was reloading and losing their typing. 25s is chosen
  to sit *under* the common 30s proxy timeout — exceeding it returns an HTML
  error page, and `response.json()` then throws a parse error that reads like a
  bug in this code rather than a timeout. Voice upload gets 120s deliberately:
  25s would abort work that was going to succeed, and the clinician cannot
  re-record a consult that already happened.
  The subtle case handled: on a **send-patient-message** timeout the message may
  have been written after we stopped waiting, so the copy says the outcome is
  unknown and to check before resending, rather than inviting a duplicate
  message to a patient.

### 7. Model 503 Outage Fallback

- **Status:** PARTIAL
- **Where:** `frontend/lib/offline_summary.ts` — `deriveOfflineFindings()`
  (line 81), threshold rules for potassium/eGFR/systolic BP with bidirectional
  negation handling (line 74). Rendered in
  `frontend/components/patient/PatientWorkspace.tsx:1487-1533` behind `conflictsDegraded`, with
  `OfflineModeBadge` ("Offline Mode (Rule-Derived)").
- **What Breaks First:** Coverage. The rules read three vital families out of
  free text; anything outside them is not assessed. This is why the panel always
  renders `offlineCoverageNote()` — "absence of a flag here does not mean absence
  of a problem" — rather than an empty list. The reasoning is that an empty
  "critical flags" panel does not read as *"we could not check"*, it reads as
  *"there are none"*, handing a clinician a false negative produced by an
  infrastructure failure nobody told them about.
  **The real gap:** the fallback is triggered only by the *contradiction* check
  failing. A 503 from `/api/ai/summarize` shows a toast and leaves the Glance
  View as it was; there is no `frontend/components/patient/GlanceCard.tsx` (the component is
  `frontend/components/glance/TopCard.tsx`) and it has no degraded path of its own. So the
  mechanism is real and demonstrable but its trigger surface is one endpoint of
  four.

### 8. Note Concurrency & Optimistic Locking

- **Status:** SURVIVES
- **Where:** `supabase/migrations/20260901_care_notes_version.sql` —
  `care_notes.version` (line 30), `save_care_note_yjs()` compare-and-swap
  (line 52). Caller: `frontend/components/editor/CareNoteEditor.tsx` — version
  captured on fallback load, CAS save, conflict alert in the render root.
- **Scoped deliberately.** OCC applies **only** on the fallback path. With
  Hocuspocus connected, Yjs merges character-level operations and there is no
  lost update — OCC there would reject merges the CRDT resolves correctly. The
  window is when collab is *down*: the editor writes the whole document with a
  plain `UPDATE`, two clinicians both write, and the second silently erases the
  first. That is the case this guards, and it is exactly the situation in a
  clinic where the collab process died.
- **What Breaks First:** The clinician's patience, not their data. Verified
  live: a second save with a stale expected version returns `NULL` and writes
  nothing. The refused save **does not retry** with the fresh version — that is
  precisely the clobber it exists to prevent. It stops, keeps the text on screen,
  and renders a persistent alert saying the note was **not** written, because the
  dangerous property is that unsaved text looks saved. The function is
  `SECURITY INVOKER`, so RLS still decides who may touch the row.

### 9. Delivery Failure Tracing for Appointment Links

- **Status:** PARTIAL *(was DOES NOT)*
- **Where:** `ai-service/services/messaging.py` — `queue_delivery()`,
  `apply_provider_status()`, `unresolved_for_clinic()`, `DeliveryRecord.confirmed_received`.
  Webhook and staff view: `ai-service/routers/messaging.py` —
  `POST /api/messaging/delivery-webhook`, `GET /api/messaging/unresolved`.
  Producer: `ai-service/routers/patient_message.py` records an attempt after a
  gate-approved send. Schema:
  `supabase/migrations/20260901_phone_identity_and_delivery.sql:95-150`.
- **What it now does.** `sent` and `delivered` are separate facts and only a
  signed provider webhook can advance past `queued` — there is no code path that
  sets `delivered` from inside this service. `confirmed_received` returns **false**
  for `sent`, because provider acceptance is our side of the handoff, the same
  category of claim as "we generated a link". Status transitions are monotonic,
  so a duplicate `sent` arriving after `delivered` is ignored (providers do not
  guarantee callback order, and a regressed status sends staff chasing a patient
  who already has the message) while a late `failed` still wins, because it is
  the truth and the earlier optimism is not. Malformed numbers are rejected
  before dispatch, since a bad number is the commonest cause of silent
  non-delivery and the carrier's rejection arrives asynchronously if at all.
  The webhook is HMAC-signed and **fails closed** with no secret configured — it
  is the one unauthenticated write path into delivery state, and a green tick
  anyone can forge is worse than no tracking.
- **What Breaks First:** No provider is wired. `_dispatch()` raises
  `NotImplementedError` and `provider_configured()` is false, so every delivery
  stays `queued` and renders as *not confirmed delivered* — honest, but no
  patient is actually contacted by SMS or WhatsApp. This is mock-first by the
  same reasoning as transcription: inventing a provider would manufacture the
  false confidence the module exists to prevent. It is PARTIAL rather than
  SURVIVES because the tracing is real and the sending is not. Wiring a provider
  is implementing `_dispatch` and pointing its webhook at the endpoint; nothing
  else changes. Verified by mutation — making `sent` count as receipt, or
  allowing status regression, each fails a test.

### 10. Maker-Checker Human Gate & Correction Loop

- **Status:** SURVIVES *(was PARTIAL)*
- **Where:** Gate: `ai-service/services/safety/patient_gate.py`;
  `POST /api/ai/send-patient-message` at `ai-service/routers/patient_message.py:195`;
  DB enforcement `AND visibility = 'internal'` on both care-team INSERT policies in
  `supabase/migrations/001_foundation.sql`.
  Retraction: `POST /api/ai/retract-patient-message`;
  `retract_patient_message()` in `ai-service/services/supabase_writer.py`;
  UI in `frontend/components/timeline/TimelineEntry.tsx` (Withdraw control,
  struck-through body, "Withdrawn by the care team" banner) and
  `handleRetractMessage` in `frontend/components/patient/PatientWorkspace.tsx`.
- **What retraction does.** The original entry is **marked, never deleted or
  edited** — the patient already read it, and a message that silently disappears
  is worse than one shown as withdrawn: they remember being told something and
  can no longer find it, and an auditor cannot reconstruct what was sent. The
  retraction is then posted as a **new patient-visible entry**, because a flag on
  the old message only reaches someone who goes back and re-reads it, which is
  exactly what a patient who already acted on it will not do. Restricted to
  clinician/admin, matching who may approve a send. The reason has a minimum
  length and is shown to the patient verbatim; a one-word reason alarms without
  informing.
- **What Breaks First:** The patient's attention, not the record. A withdrawal
  notice competes with the original message in the same timeline, and nothing
  here forces them to read it — the system can correct the record and notify,
  but cannot confirm the correction landed. That confirmation depends on item 9,
  which is why the two are coupled: with no provider wired, a retraction notice
  is portal-only for a patient who may never log in.

### 11. Conflict Engine for Nurse vs. Patient Timeline Entries

- **Status:** PARTIAL
- **Where:** `ai-service/services/safety/clinical_conflict.py`; endpoint
  `POST /api/ai/conflicts`; 26 tests in `ai-service/tests/test_conflicts_endpoint.py`.
  Surfaced in `frontend/components/patient/PatientWorkspace.tsx` via `conflicts` state and the contradiction
  badge.
- **What Breaks First:** Precedence. The engine detects cross-author
  contradictions and reports **both** assertions with their authors — the
  docstring at line 165 is explicit that it "reports the delta; a clinician
  decides". It does **not** rank a manual nurse entry above an unverified AI
  extraction, which is what the audit asks for. So a "Penicillin allergy" recorded
  by a nurse and "No known drug allergies" extracted by AI are surfaced as a
  symmetric disagreement rather than one with a presumptive winner.
  The detection itself is solid — deduplication keys on **value** rather than
  `(author, value)` so eight people saying 10mg collapse to one; titration is
  suppressed by asking whether anyone asserted a value the prescriber never did,
  not by dose ordering — and the flag is persistent on the clinician view. What
  is missing is the ordering, and with the AI-extraction side unranked a tired
  clinician resolving quickly has no cue which side carries more weight.

### 12. Verifiable Confidence & Risk Metrics

- **Status:** SURVIVES
- **Where:** `ai-service/services/safety/confidence.py` — formula
  `0.50 × agreement + 0.35 × verification + 0.15 × rules`, abstention below 0.60.
  Stored as three *separate* columns in `supabase/migrations/001_foundation.sql`:
  `confidence_score`/`confidence_band` (system reliability),
  `importance_score` (workflow urgency), `risk_level` (clinical severity), with
  `risk_floor` and `model_risk` kept apart so a badge can always show which set
  the level. Documented in `TECHNICAL_BRIEF.md` §4.3.
- **What Breaks First:** Nothing observed. The design point is the separation:
  collapsing these into one number is the "decoration" failure — importance
  rendered as confidence looks like a trust signal while actually measuring queue
  position. `abstained` is a real column and an abstained highlight is withheld
  from the glance view unless critical, so low confidence produces **absence or
  an explicit marker**, never an invented percentage. The formula's weights are
  a judgement call and are not empirically calibrated against outcomes — they are
  documented, reproducible and inspectable, which is what "verifiable" can mean
  in a 72-hour build, but they are not validated.

### 13. Unbiased Ranking Loop Safeguards

- **Status:** SURVIVES
- **Where:** `ai-service/services/importance.py` — `ABSOLUTE_FLOOR` (critical
  ≥ 0.90) and `NO_DEMOTION_SEVERITIES` (line 54), applied at lines 394-420.
  Four tests in `ai-service/tests/test_self_learning_importance.py::TestSafetyFloorAgainstFatigue`.
- **The gap was real before this.** `critical` contributes only
  `RISK_LEVEL_WEIGHT × 1.0 = 0.30`, so a critical highlight with a learned weight
  driven negative by dismissal landed near 0.30 — **below** a merely `medium`
  item that was recent and frequently engaged with. `reject` carries −0.3, and
  the clinician most likely to dismiss the same alert forty times is a tired one
  at the end of a list. So the signal the loop learned from was fatigue, and the
  item it learned to hide was the alert that kept firing.
- **What Breaks First:** Nothing, and the *first* fix here was wrong in an
  instructive way. Clamping `high` to a constant 0.70 made the learning loop
  **inert** for high-risk items — every floored item landed on the same number,
  destroying ordering — and it broke a legitimate existing test, which is how it
  was caught. The shipped design separates the two: an absolute floor for
  `critical` only (rare, must always surface), and for `high` a *relative* rule —
  learning may raise the score but never push it below its own unlearned value.
  `medium` and `low` stay fully demotable, which is the point of the loop:
  clinicians genuinely know which routine items are noise.

### 14. Addressable Highlight Provenance

- **Status:** PARTIAL *(was PARTIAL — different gap)*
- **Where:** `ai-service/services/provenance.py` — `normalise_quote()`,
  `quote_hash()`. Populated at insert in `ai-service/routers/scribe.py:117`
  (`note_version`) and `:293-294` (`source_note_version`, `exact_quote_hash`).
  Schema and `highlight_source_changed()`:
  `supabase/migrations/20260901_care_notes_version.sql:80-110`.
  UI: `isSourceModified()` in `frontend/lib/types/index.ts:175`, rendered as a
  "Source Modified" tag in `frontend/components/glance/CriticalFlags.tsx`, with
  `currentNoteVersion` threaded from the SSR query through `TopCard`.
- **What it now does.** The hash covers the **quote**, not the whole entry.
  Hashing the entry would invalidate every highlight derived from it whenever any
  unrelated sentence changed, and that noise makes the signal worthless.
  Normalisation folds whitespace and case only — a clinician reflowing a
  paragraph has not changed what the note says, while `10mg` and `1.0mg` hash
  differently. `isSourceModified` fails toward **modified**: a null recorded
  version means the highlight predates tracking and freshness cannot be asserted,
  so the UI degrades to a visible tag rather than a silent claim of currency.
- **What Breaks First:** Coverage of the write path. Only the scribe route
  populates these fields; highlights created through other paths still store
  `NULL`, and every pre-existing highlight will render "Source Modified" until
  re-extracted. That is the intended failure direction — over-warning rather than
  falsely reassuring — but on a seeded database it means the tag appears on
  everything, which erodes the signal it is meant to carry. `exact_quote_hash` is
  stored but not yet **compared** at read time; staleness is currently detected by
  version alone, so an edit that leaves the version untouched would not be caught.

### 15. (see item 13 — Unbiased Ranking Loop Safeguards)

Numbered 13 in this document to match the audit's own numbering of the
refactoring steps; the ranking safeguard is a single item.

### 16. (see item 14 — Addressable Highlight Provenance)

As above.

---

## Summary

| # | Item | Status |
|---|---|---|
| 1 | Phone/WhatsApp identity | **SURVIVES** — OTP *and* token links to a real session |
| 2 | Multi-clinic RLS isolation | **SURVIVES** |
| 3 | Log scrubbing | PARTIAL — structured IDs only |
| 4 | Verifiable redaction path | **SURVIVES** |
| 5 | SEA code-switching | PARTIAL — untested against real audio |
| 6 | Timeout guardrails | **SURVIVES** |
| 7 | 503 outage fallback | PARTIAL — one trigger surface of four |
| 8 | Note concurrency (OCC) | **SURVIVES** |
| 9 | Delivery tracing | PARTIAL — live Telegram provider; reach requires patient opt-in |
| 10 | Maker-checker + retraction | **SURVIVES** |
| 11 | Conflict engine precedence | PARTIAL — detects, does not rank |
| 12 | Confidence metrics | **SURVIVES** |
| 13 | Ranking safety floor | **SURVIVES** |
| 14 | Highlight provenance | PARTIAL — verified at read, scribe write path only |

**If this went live tomorrow, the first thing to break is still item 9 — but for
a different reason than before.** Delivery is now traced honestly rather than not
at all: `sent` never renders as received, out-of-order webhooks cannot regress a
status, and a bad number is rejected before dispatch. What is missing is the
provider itself, so every delivery sits at `queued` and no patient is contacted
outside the portal. That is a visible failure rather than a silent one, which is
the improvement; it is still a patient not getting their appointment details.

Item 1 compounds it exactly as before: the patient least likely to open a portal
is the one with no email, registered under an invented address, and unreachable
by SMS because no provider is wired.

The three items I would fix before a clinic used this, in order: wire a real
messaging provider (9), then phone-based access so the portal is reachable at all
(1), then compare `exact_quote_hash` at read time so provenance catches edits that
leave the version untouched (14).

## Changelog

**First pass** — six SURVIVES, seven PARTIAL, one DOES NOT.

**Second pass** — items 9, 10 and 14 were the three gaps flagged as "schema
without behaviour". All three now have behaviour: delivery tracing with a signed
webhook and monotonic status, retraction end-to-end from a Withdraw control to a
patient-visible notice, and populated highlight provenance with a "Source
Modified" tag. Item 10 moves to SURVIVES; 9 and 14 remain PARTIAL with the
remaining gap named in each. 420 tests pass; delivery semantics and retraction
were checked by mutation rather than assumed.

**Third pass** — mock provider, OTP flow, read-time provenance comparison.

Item 9's loop now closes: dispatch to a reserved test range, an asynchronous
callback over the real signed HTTP webhook, monotonic status applied. Verified
end to end against a live uvicorn instance — `delivered`, `failed`, dispatch
rejection, and the `silent` case that stays at `sent`. The mock has no
privileged path into delivery state, asserted by AST rather than grep. Still
PARTIAL: no live provider, so no real patient is contacted.

Item 1 gains `POST /api/auth/request-otp` and `POST /api/auth/verify-otp` —
peppered, phone-bound hashes; 5-minute TTL; capped issuance *and* attempts;
identical responses for known and unknown numbers so the endpoint cannot be used
to enumerate a clinic's patients. Still PARTIAL: verification returns the
identity but does not mint a Supabase session, so the fake-email workaround is
removed at the identity layer without a completed sign-in.

Item 14 gains `verify_quote()` with a four-state verdict. The motivating case —
a dose edited from 10mg to 100mg *within the same care-note version* — now
reports MODIFIED where version comparison alone said CURRENT. Still PARTIAL:
only the scribe path writes the hash.

371 tests. All three checked by mutation.

**Fourth pass** — session minting. `verify-otp` now returns a real GoTrue
session (access + refresh token), so a phone number is a complete sign-in and
the fake-email workaround has no remaining motive. Item 1 moves to SURVIVES.

The self-signed HS256 shortcut was measured against this project and **works
today** — the legacy symmetric secret is still enabled alongside the ES256 keys
the project publishes. It was refused anyway: a self-signed token has no session
to revoke, no refresh token for `@supabase/ssr` to maintain, and stops verifying
on the day Supabase disables symmetric secrets, which is a total patient lockout
at a date nobody here controls. Sessions come from GoTrue's admin magiclink
exchange instead, redeemed with the anon key so the session cannot inherit
service-role authority.

GoTrue requires an email, which is met with a deterministic sentinel at a
`.invalid` domain (RFC 2606, cannot resolve). That is not the vulnerability this
flow closed: the sentinel is generated by the system, never typed by staff,
never a login factor, never a delivery channel, and unique by construction —
where an invented front-desk address was routable, collidable, and a false claim
of reachability.

**Live-verified.** The full exchange was run against the project's own Supabase
instance and cleaned up after itself:

```
1. provision phone-only user -> 200   (sentinel .invalid address, phone recorded)
2. generate_link             -> 200
3. verify (anon)             -> 200   role=authenticated, alg=ES256, refresh_token present
4. care_notes visible        -> 0     (no profile row, so RLS denies)
5. cleanup: delete user      -> 200
```

The returning-patient branch too: a second provision returns 422, and the
fallback lookup finds the same user id — so an existing patient signs in rather
than being told their account could not be created.

Running it found a bug reading could not. The first implementation redeemed with
`token`, which GoTrue treats as a plaintext OTP and rejects for lacking an
accompanying email or phone; an admin-generated link carries `token_hash`. Both
spellings look plausible and the endpoint answers a bare 400. There is now a
regression guard, itself mutation-checked — and the first version of that guard
passed for the wrong reason, because it sliced to the first `}` and landed
inside the headers dict.

`alg: ES256` in the returned token confirms the earlier decision: the session is
signed with the key the project publishes, not the legacy symmetric secret a
self-minted token would have used.

**Fifth pass** — Telegram provider and token identity.

Item 9 gains a real provider. `_dispatch` now sends via the Telegram Bot API
(async, so it does not stall the event loop), and `POST
/api/messaging/telegram-webhook` advances status from Telegram's own callbacks,
authenticated with `X-Telegram-Bot-Api-Secret-Token` and failing closed at 403
without the secret. Still PARTIAL, and the reason is a platform constraint rather
than an omission: **Telegram cannot message a phone number.** A bot may only send
to a `chat_id`, which exists only after the patient opens the bot themselves. So
an un-linked patient is genuinely unreachable on this channel, and the system
reports that rather than rerouting.

Item 1 gains the link path that makes the above reachable — `POST
/api/auth/patient-link` mints a token and returns **both** a `t.me` deep link and
a `/portal/login?token=` URL, because "reachable on WhatsApp" does not imply "has
Telegram". `POST /api/auth/redeem-token` exchanges it for a real GoTrue session.
Only the SHA-256 hash is stored; every failure mode returns one indistinguishable
message; non-patient roles are refused.

**KISS UI pass.** The four degraded states now say what they mean in words rather
than signalling by colour: `Offline Mode (Rule-Derived)`, `SAVE BLOCKED`,
`[WITHDRAWN BY CARE TEAM]`, `[SOURCE EDITED — VERIFY NOTE]`. The governing case is
that an empty flags list reads as "there are none" while a banner reads as "this
was not checked" — opposite clinical actions. And a rejected save never clears the
editor: the clinician's words are the only copy that exists.
