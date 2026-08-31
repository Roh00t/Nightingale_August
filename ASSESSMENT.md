# Nightingale — Clinical Resilience Self-Assessment

Sixteen failure modes a real clinic produces, and what this system actually does
when it meets them. Written against the code as it stands on `master`, verified
by running it rather than by reading it.

**Verification basis:** 332 tests pass (`cd ai-service && .venv/bin/python -m pytest tests/ -q`);
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

- **Status:** DOES NOT
- **Where:** `supabase/migrations/20260901_phone_identity_and_delivery.sql:95-150`
  creates `message_deliveries` with a `status` enum deliberately starting at
  `queued` (never `sent`), `failure_reason`, per-channel `destination`, and a
  partial index on unresolved rows. **Nothing writes to it.** `services/messaging.ts`
  does not exist; there is no SMS/WhatsApp provider integration, no webhook
  handler, and no delivery UI anywhere in the codebase.
- **What Breaks First:** The clinical assumption the audit names, entirely
  unmitigated. Staff send a patient a link, the UI says it sent, and the patient
  never receives it — wrong number, handset off, provider drop — and **nothing
  in this system can tell the difference between "delivered" and "generated"**.
  The table encodes the right model (our side of the handoff proves nothing about
  receipt; only a provider webhook advances status, which is why no user role has
  INSERT/UPDATE on it) but a schema catches no failures. This is the weakest item
  in the audit and is stated as DOES NOT rather than PARTIAL because no delivery
  is traced at all.

### 10. Maker-Checker Human Gate & Correction Loop

- **Status:** PARTIAL
- **Where:** Gate: `ai-service/services/safety/patient_gate.py`; endpoint
  `POST /api/ai/send-patient-message` at `ai-service/routers/patient_message.py:195`;
  DB enforcement `AND visibility = 'internal'` on both care-team INSERT policies
  in `supabase/migrations/001_foundation.sql`. UI: `frontend/components/patient/PatientWorkspace.tsx` `handleSendPatientMessage`
  and the blocked-state chips. Retraction columns: `is_retracted`, `retracted_at`,
  `retracted_by`, `retraction_reason` at
  `supabase/migrations/20260901_phone_identity_and_delivery.sql:152-156`.
- **What Breaks First:** Retraction — it does not exist above the schema.
  `grep -rn is_retracted frontend` returns **nothing**: no retract control, no
  timeline treatment, no patient-facing retraction event. A clinician who sends a
  wrong message has no way to withdraw it.
  The approval half **is** enforced and tested (12 assertions in
  `ai-service/tests/test_patient_message_gate.py`, plus 4 DB-level bypass tests). Three
  properties make it real rather than decorative: the *edited* text is screened
  at the moment of Send; grounding sources are read **server-side** so a
  fabricated dose cannot be sent as its own grounding; and the write happens only
  on the passing branch of the same call, so a clinician's own token in curl
  cannot create a patient-visible row. Checked by mutation — ignoring the
  verdict fails 6 tests, removing the RLS clause fails 2.

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

- **Status:** PARTIAL
- **Where:** `supabase/migrations/20260901_care_notes_version.sql:80-110` adds
  `highlights.source_note_version` and `exact_quote_hash`, plus
  `highlight_source_changed()` which derives staleness on read rather than
  storing a flag. `source_entry_id` and `provenance_pointer` already existed in
  `supabase/migrations/001_foundation.sql`.
- **What Breaks First:** The columns are never populated.
  `grep -rn exact_quote_hash ai-service` returns **nothing** — the extraction
  pipeline does not compute the hash or record the version, so
  `source_note_version` is `NULL` on every existing and newly created highlight.
  `highlight_source_changed()` deliberately reports `NULL` as *changed*, so the
  system degrades to showing "Source Modified" rather than asserting a freshness
  it cannot support — but that means it would currently mark **everything**
  stale, and no UI reads it yet. So the mechanism is designed and the storage
  exists; the behaviour does not. Click-through to the source entry works today
  and lands on current text with no staleness signal, which is the silent failure
  the audit describes.

### 15. (see item 13 — Unbiased Ranking Loop Safeguards)

Numbered 13 in this document to match the audit's own numbering of the
refactoring steps; the ranking safeguard is a single item.

### 16. (see item 14 — Addressable Highlight Provenance)

As above.

---

## Summary

| # | Item | Status |
|---|---|---|
| 1 | Phone/WhatsApp identity | PARTIAL — schema only, no flow |
| 2 | Multi-clinic RLS isolation | **SURVIVES** |
| 3 | Log scrubbing | PARTIAL — structured IDs only |
| 4 | Verifiable redaction path | **SURVIVES** |
| 5 | SEA code-switching | PARTIAL — untested against real audio |
| 6 | Timeout guardrails | **SURVIVES** |
| 7 | 503 outage fallback | PARTIAL — one trigger surface of four |
| 8 | Note concurrency (OCC) | **SURVIVES** |
| 9 | Delivery tracing | **DOES NOT** |
| 10 | Maker-checker + retraction | PARTIAL — retraction absent |
| 11 | Conflict engine precedence | PARTIAL — detects, does not rank |
| 12 | Confidence metrics | **SURVIVES** |
| 13 | Ranking safety floor | **SURVIVES** |
| 14 | Highlight provenance | PARTIAL — columns never populated |

**If this went live tomorrow, the first thing to break is item 9.** Every other
gap degrades visibly — an unpopulated provenance column shows "Source Modified",
an unranked contradiction still shows both sides, a missing offline trigger still
leaves the record readable. Delivery is the only one that fails *silently and
confidently*: staff see "sent", the patient received nothing, and no part of this
system can tell those apart. Item 1 compounds it, because the patient most likely
to be unreachable is the one with no email who was registered under an invented
address.

The three items I would fix before a clinic used this, in order: delivery
tracing (9), retraction (10), then populating highlight provenance (14).
