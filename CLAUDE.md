# Nightingale — Master Plan

Real-time collaborative clinical note system. Four processes, one Postgres, zero-trust posture.

> **Governance:** every rule in `guardrails.md` is binding on all work in this repo.
> Read it before writing code. It wins over convenience, speed, and over this file.

---

## 1. Architecture

```
Browser (Next.js 15, App Router)
  ├── HTTPS ──→ Supabase        Auth + Postgres + RLS + Realtime
  ├── WSS ────→ Hocuspocus      Yjs CRDT sync, JWT auth        :1234
  └── HTTPS ──→ FastAPI         PHI redaction + Groq LLM       :8000
```

| Service | Path | Dev command | Port |
|---|---|---|---|
| Frontend | `frontend/` | `npm run dev:frontend` | 3000 |
| Collab | `collab-server/` | `npm run dev:collab` | 1234 |
| AI | `ai-service/` | `npm run dev:ai` | 8000 |

**Environment is two files, not one.** The repo-root `.env` is loaded explicitly by the AI
service (`main.py`) and the collab server (`index.ts`). Next.js reads env from its own project
root, so `frontend/.env.local` is required and independent. Neither substitutes for the other.

**`npm run dev:ai` is broken as written.** It invokes a bare `uvicorn`, which resolves to a
system Python without FastAPI. Fix the script to `.venv/bin/uvicorn` or activate the venv first.

---

## 2. Current state

An audit of the inherited template established the following. Treat these as facts about the
starting point, not as speculation.

**Working:** both TypeScript projects typecheck clean; the AI service boots and serves
`/health` and `/ready`; the Groq router handles retries and error mapping correctly;
trust badges and the collab "Local Only" fallback are implemented as documented;
schema indexing is sound (composites lead with `care_note_id`).

**Broken or absent — each is a phase input:**

| Finding | Phase that fixes it |
|---|---|
| Redaction has no name detection; Presidio/spaCy not installed or declared | 2 |
| `/api/auth/patient-login` resets any patient password unauthenticated | 1 |
| `verifyCareNoteClinicScope` checks clinic but never role | 1 |
| Migration 014 reverts 007's RLS fix and drops 012's patient readback | 1 |
| `seed_demo_data` 4-arg version shadows the 8-arg one; Sunrise never seeds | 1 |
| Migration 013 re-hardcodes `care_plan_score: 0.78`, undoing 008 | 1 |
| No SSR and no React Query; `@tanstack/react-query` declared, never imported | 4 |
| `changed_by = "system"` and archival `user_id = '00000000-…'` into uuid FKs | 1, 3 |
| `createNoteVersion` races its own `UNIQUE(care_note_id, version_number)` | 3 |
| `_compute_learned_score` ignores `patient_id`; pools signal across clinics | 4 |
| No auth on any AI endpoint | 2 |
| 39 of 40 tests error at fixture setup on blank credentials | 5 |

---

## 3. OPEN DECISION — blocks Phase 1

Phase 1 names the schema `users`, `patients`, `versions`. The existing code uses
`profiles`, `note_versions`, and has **no `patients` table** — a patient is a `profiles` row
with `role = 'patient'`, and `care_notes.patient_id` points at `profiles.id`.

Renaming touches 26 call sites (`profiles` ×9, `note_versions` ×4, plus `interaction_log`)
and breaks the explicit PostgREST join hints the frontend depends on, e.g.
`profiles!timeline_entries_author_profile_fkey`.

**Recommendation: keep `profiles` and `note_versions`; do not add a `patients` table.**
The existing shape is already correct — one identity table with a role discriminator, which is
what the RLS helper functions `get_user_role()` and `get_user_clinic_id()` are built on.
Splitting `patients` out would duplicate the clinic scoping and give RLS two paths to defend.

Resolve this before writing `001_foundation.sql`. If the rename is required anyway, it is a
Phase 1 task in its own right, with the frontend query updates in the same commit — never
a rename that lands ahead of its call sites.

---

## 4. Phases

Sequential. A phase is not startable until the previous one's exit criteria pass.
See `guardrails.md` §2 for the gate procedure.

### Phase 1 — Foundation: zero-trust data & auth

Squash `001`–`014` into a single `001_foundation.sql` expressing the *intended final state*,
not the historical patch sequence. The current chain is the cautionary tale: 014 reverted 007,
013 reverted 008, 012's fix was dropped silently. One file, one definition per policy.

- Core schema: `clinics`, `profiles`, `care_notes` (with `glance_cache` jsonb),
  `timeline_entries`, `highlights`, `note_versions`, `comments`, `interaction_log`
- RLS for `patient` / `staff` / `clinician` / `admin` on every table, no exceptions
- Patients cannot read internal comments or raw AI-scribed entries
- Patients can read back their own `patient_message` entries (the clause 012 added, 014 lost)
- Clinic-scoped access via `SECURITY DEFINER` helpers — never a nested `EXISTS` on an
  RLS-protected table (see `guardrails.md` §4)
- Fix `/api/auth/patient-login`: require an authenticated clinician/admin session, or delete
  the route. Model it on `app/api/patients/route.ts`, which already does this correctly.
- Fix `verifyCareNoteClinicScope`: clinic match **and** an explicit role allowlist that
  rejects `patient`
- Seed function: one definition, both clinics, `care_plan_score` on the 0–100 scale

**Exit:** RLS proven by the Phase 5 `test_rbac_scope.py` assertions running green against a
seeded project; both clinics present; a patient JWT rejected by the collab server.

### Phase 2 — AI service & hardened redaction

- Add `presidio-analyzer`, `presidio-anonymizer`, `spacy` to `requirements.txt` **and**
  `pyproject.toml`; pin `en_core_web_sm` acquisition in the setup path
- Presidio + spaCy NER for PERSON, layered with the existing regex recognizers
  (the SG NRIC pattern `[STFGM]\d{7}[A-Z]` is correct — keep it, including the M series)
- Validate against NRIC, SG phone formats, **and full names** before any Groq call
- Authenticate every AI endpoint — currently all four are open
- AI Scribe ingestion endpoint: writes `author_role = 'system'` and a `provenance_pointer`
  referencing the source

**Exit:** a redaction test asserting `Alice Wong` and `Dr. Sarah Chen` are replaced with
`<PERSON_n>` placeholders. This is the specific failure the audit demonstrated; it is the
acceptance test.

### Phase 3 — Real-time sync & inline collaboration

- Hocuspocus + TipTap + Yjs CRDT
- Concurrency: AI updates must never clobber a human clinician's in-flight edit
- Flush Yjs state to `note_versions` for diff and revert
- Fix `changed_by` — a uuid FK cannot take the string `"system"`
- Fix version numbering — compute the next number inside the insert, not read-then-write

**Exit:** `test_concurrent_edits.py` green; concurrent flushes do not violate
`UNIQUE(care_note_id, version_number)`.

### Phase 4 — Next.js UI & Glance View

- Glance View ≤300ms warm: a single indexed read of `care_notes.glance_cache`,
  server-rendered. The page is currently `'use client'` with a `useEffect` waterfall — that
  is the thing being replaced.
- Either adopt `@tanstack/react-query` properly or remove the dependency; do not leave it
  declared and unused
- Self-learning importance loop: pin/accept updates historical weightings
- Scope the learned signal per clinic — `_compute_learned_score` currently ignores its
  `patient_id` argument and pools across tenants

**Exit:** warm-path measurement recorded with method and sample size; `test_self_learning_importance.py` green.

### Phase 5 — Automated testing

Five suites: `test_rbac_scope.py`, `test_revision_history.py`, `test_highlight_provenance.py`,
`test_concurrent_edits.py`, `test_self_learning_importance.py`.

These are **integration** tests — every fixture builds a live Supabase client and signs in.
They need a reachable seeded project. 40 tests currently collect; 1 passes.

**Exit:** all five suites green, with the run output pasted into the phase sign-off.

---

## 5. Commands

```bash
npm install                                        # workspaces: frontend + collab-server
cd ai-service && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m spacy download en_core_web_sm  # Phase 2

npm run dev                                        # all three (see dev:ai caveat above)
cd ai-service && .venv/bin/python -m pytest tests/ -v
cd frontend && npx tsc --noEmit
./scripts/seed.sh                                  # reads root .env
```

---

## 6. File map

```
supabase/migrations/     001_foundation.sql after Phase 1 squash
ai-service/
  services/redaction.py  PHI pipeline — Phase 2 rewrite target
  services/llm.py        Groq client, retries, JSON mode
  services/importance.py self-learning scoring
  routers/               summarize · highlights · redact · patient_message
  tests/                 the 5 suites
collab-server/
  auth.ts                JWT verify (ES256 JWK or HS256 secret) + profile lookup
  persistence.ts         clinic scope, Yjs load/save, version snapshots
frontend/
  app/(dashboard)/patients/[id]/page.tsx   Glance View — Phase 4 target
  app/api/auth/patient-login/route.ts      Phase 1 security fix
  lib/yjs/provider.ts    Hocuspocus client
```

---

## 6b. Messaging, identity and UI rules

### Telegram environment

| Key | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot credential. Absent → dispatch **refuses**; it never falls back to a no-op. |
| `TELEGRAM_BOT_USERNAME` | For `t.me/<Bot>?start=<token>` deep links. |
| `TELEGRAM_WEBHOOK_SECRET` | Compared against `X-Telegram-Bot-Api-Secret-Token`. Absent → webhook **403s**. |
| `MESSAGING_PROVIDER` | `telegram` \| `mock` \| unset. Unset means no provider and deliveries stay `queued`. |
| `PATIENT_PORTAL_URL` | Base for `/portal/login?token=…`, the non-Telegram path. |

### Passwordless identity — the constraint that shapes it

**Telegram cannot message a phone number.** A bot may only send to a `chat_id`,
which exists only after the person opens the bot themselves. There is no API to
initiate contact. That is the consent model, not an obstacle, and any code that
implies otherwise is describing something the platform does not do.

So reaching a patient who has no email is a two-step flow:

1. Front desk calls `POST /api/auth/patient-link` → token + both links.
2. Patient taps `t.me/<Bot>?start=<token>` (or opens the portal link in any
   browser — "reachable on WhatsApp" does not imply "has Telegram").
3. Telegram delivers `/start <token>` **with** the chat_id; the webhook binds it.
4. Only now can the clinic message them.

Rules for this path:

- Store **only** the SHA-256 hash. The table is staff-readable for support, and a
  plaintext token there is a credential lying in the open.
- Tokens use `secrets.token_urlsafe`, because Telegram's start parameter permits
  only `[A-Za-z0-9_-]` and ≤64 chars. A base64 token with `+ / =` silently loses
  characters and the patient gets "link no longer valid".
- **Every** redemption failure returns one message. Expired, consumed, unknown
  and attempt-exhausted must be indistinguishable — each distinction is an
  oracle, and "unknown" lets someone probe for valid tokens.
- `redeem_token` refuses any profile whose role is not `patient`. A token minted
  against a staff profile would be a password-free path into a clinical account.

### KISS UI rules — the reader is exhausted and in pain

Every degraded state is stated **in words**, never implied by a colour or an
absence. The governing case: a clinician reading an empty flags list concludes
*"there are none"*; one reading a banner concludes *"this was not checked"*.
Those are opposite clinical actions.

| State | Required treatment |
|---|---|
| AI unavailable | Amber bordered banner: **"Offline Mode (Rule-Derived) — Absence of a flag does not imply absence of clinical concern."** |
| OCC save rejected | Red banner: **"SAVE BLOCKED: Another user updated this note."** The draft is **never** cleared, reset or re-fetched over. |
| Retracted message | Solid red **[WITHDRAWN BY CARE TEAM]** badge, heavy strike-through, verbatim reason below. |
| Stale provenance | Solid orange **[SOURCE EDITED — VERIFY NOTE]** badge. |

Constraints: no gradient-only signalling, no icon-only meaning, no hover-to-reveal
for anything clinical, and uppercase bold for the four states above. A tired
reader must not have to interpret.

**Never destroy user text to show an error.** A rejected save means their words
are the only copy that exists.

### Tests

```bash
cd ai-service && .venv/bin/python -m pytest tests/test_telegram_messaging.py -v
```

---

## 7. Traps

- `.env.demo` ships every value **blank**. Blank is worse than absent: it defeats the
  `os.getenv(key, default)` fallbacks in `conftest.py`, so tests fail with
  `supabase_url is required` instead of trying localhost.
- Seed passwords are `demo-password-123` (from `scripts/seed.sh`). The README's
  `demo-clinician-2026` values do not exist. Three files disagree; the script is authoritative.
- npm workspaces hoist to the root `node_modules`. Empty `frontend/node_modules` is correct.
- `README.md` references `.env.example` and `supabase/seed.sql`. Neither exists.
- The versions table is `note_versions`. The brief calls it `versions`. See §3.
- **Migration filenames must carry a full 14-digit timestamp.** The Supabase CLI
  keys `schema_migrations` on the leading digit-run, so `20260901_a.sql` and
  `20260901_b.sql` both resolve to version `20260901` and the second insert dies
  on the primary key. Seven files once collapsed into two keys and `supabase
  start` failed at migration 3 of 8 — on the very command the README and demo
  script open with. Renamed to `YYYYMMDDHHMMSS_name.sql` on 3 Sep 2026.

---

# 8. Clinical failure-scenario audit — 2 Sep 2026

Sixteen real-world scenarios, assessed against the code as it stands. Line
numbers were verified by grep at the time of writing, not recalled.

**Six SURVIVE, seven are PARTIAL, three DO NOT.** That distribution is the finding.
Where something is absent it is named as absent, because a clinician calibrates
trust to what they believe the system does, and an overclaimed control is more
dangerous than a missing one.

---

### 1. Phone-only / WhatsApp patient (no email)

- **Status:** PARTIAL
- **Location:** Backend complete — `ai-service/routers/auth.py:41`
  (`redeem-token`), `:108` (`patient-link`, returns both a `t.me` deep link and a
  portal URL), `ai-service/services/telegram_identity.py:50` (SHA-256 hash only).
  Session minting is live-verified end to end against GoTrue.
  **ABSENT: any user interface.** `grep` finds **zero** frontend call sites for
  `patient-link` or `redeem-token`, and **ABSENT: `frontend/app/portal/login`** —
  the route the portal link points at does not exist.
- **What breaks first:** The front desk. Staff have no button to generate a link,
  so they fall back to inventing an email exactly as before. If they call the API
  by hand, the patient taps the portal link and gets a 404. The identity model no
  longer *requires* email — but nothing a human touches reflects that yet.
- **Build it better:** A "Send access link" action on the patient record calling
  `POST /api/auth/patient-link`, and a `/portal/login` page that posts the token
  to `redeem-token` and calls `supabase.auth.setSession()`. Both are small; their
  absence is the whole gap.

### 2. Multi-tenant isolation — one line has a bug

- **Status:** SURVIVES
- **Location:** Primary — `supabase/migrations/001_foundation.sql:296`
  (`get_user_clinic_id()`, a `SECURITY DEFINER` read of `profiles`). Secondary —
  `supabase/migrations/20260901000003_multi_clinic_rls.sql:121`, `RESTRICTIVE` tenant
  policies over a denormalised `clinic_id` filled by trigger.
- **How many patients leak if that line fails:** With one barrier it would be
  every patient in every other clinic. There are two independent ones: the
  helper, and a per-row `clinic_id` the caller cannot forge. Both must be wrong
  simultaneously. `RESTRICTIVE` is load-bearing — Postgres ORs *permissive*
  policies, so adding these as permissive would have widened access.
- **What could still break it:** The service-role key bypasses RLS entirely. 21
  call sites re-implement the checks by hand (`guardrails.md` S3); a single one
  forgetting `resolve_care_note()` is a cross-tenant write with no second barrier.

### 3. PHI in logs, traces and third-party dashboards

- **Status:** PARTIAL
- **Location:** `ai-service/services/log_scrubbing.py:97`
  (`PHIAnonymizingLogFilter`), installed on the **root** logger at
  `ai-service/main.py:74` — before the app is constructed, so uvicorn access logs
  and third-party tracebacks are covered too.
- **What breaks first:** A free-text name. NRIC, SG phone and email are
  structural and caught anywhere; names are only caught in *labelled* positions
  (`patient: X`, `name=X`). `"Patient Alice Wong called"` passes through
  unscrubbed — verified. A bare capitalised-word rule was rejected because it
  eats "Lisinopril" and "Monday", which gets the filter switched off.
- **Retention:** Not controlled by this repo. Railway retains stdout per its own
  policy; there is no log-shipping configuration, no scrubbing at rest, and
  **ABSENT: any documented retention or review period.**
- **Build it better:** Ship logs to a sink with a defined retention window, and
  add a name deny-list built from `profiles.display_name` per clinic — precision
  without the false positives of a generic rule.

### 4. Redaction strictly precedes the model

- **Status:** SURVIVES
- **Location:** The chokepoint is `ai-service/services/llm.py:127`
  (`assert_safe_for_model(messages)`), seven lines before the **only**
  `chat.completions.create` in the codebase at `:134`. Guard implementation:
  `ai-service/services/egress_guard.py:100`.
- **Proof of ordering:** Structural rather than procedural. Every model call in
  the service funnels through `_call_with_retry`; the guard re-reads the outbound
  payload there and **raises rather than repairs**, because silently fixing one
  leaked field would hide the call path that skipped redaction. A new endpoint
  cannot bypass it without deleting that line.
- **What could break it:** The guard matches NRIC and SG phone only. An
  un-redacted free-text name would pass — that remains Presidio's job upstream,
  and the module says so rather than implying more.

### 5. Clinic B onboards Monday

- **Status:** SURVIVES — config only, no schema change
- **Location:** `supabase/migrations/001_foundation.sql:823` (`seed_demo_data`
  already provisions two clinics); every scoped table carries `clinic_id`.
- **Required to serve a second clinic:** one `INSERT INTO clinics`, then
  `profiles.clinic_id` set per user. **No schema migration.**
- **What breaks first:** Not isolation — branding and configuration. There is
  **ABSENT: any per-clinic settings table**, so clinic name, logo, timezone,
  message templates and the Telegram bot are global. Clinic B's patients receive
  messages from Clinic A's bot identity.
- **Build it better:** A `clinic_settings` table keyed on `clinic_id`, read
  through the existing scoping helper.

### 6. Trilingual utterance (Malay + English + Hokkien)

- **Status:** PARTIAL
- **Location:** `ai-service/services/transcription.py:244` — `language_code` is
  deliberately **not** pinned; `ai-service/services/llm.py:45`
  (`CODE_SWITCHING_GUIDANCE`) instructs the summariser to translate meaning,
  quote the original term, and mark anything uncertain `[unclear]` rather than
  dropping it.
- **What breaks first:** Downstream extraction. The safety layer's grounding and
  risk rules are English regex — `services/safety/risk_rules.py` matches
  `K+ 6.4`, not "gula darah tinggi". A code-switched dose survives transcription
  and summarisation but will not trigger a deterministic risk floor.
- **Untested against real audio.** The `elevenlabs` package is not installed and
  live transcription is off by default, so only the mock path has ever run. The
  instruction is plausible and unverified.

### 7. Allergy stated at minute two of a twenty-minute consult

- **Status:** DOES NOT
- **Location:** **ABSENT: streaming ASR.** `ai-service/routers/transcribe.py:153`
  accepts a complete audio file and returns after the whole transcript is
  processed. There is no partial-hypothesis path, no incremental extraction.
- **What breaks first:** Nothing visibly — and that is the danger. The clinician
  gets a correct allergy flag eighteen minutes after it was said, having already
  prescribed. The system looks like it worked.
- **Deliberate trade-off:** Streaming ASR needs partial-hypothesis handling, and
  a partial transcript asserting "no known allergies" before the correction
  arrives is a worse artefact than a late-but-complete one. Not attempted rather
  than half-built.

### 8. Model hangs for 45 seconds mid-consult

- **Status:** SURVIVES
- **Location:** `frontend/lib/ai_client.ts:18` (`AI_TIMEOUT_MS = 25_000`,
  `AbortController`); `ai-service/services/llm.py:78`
  (`GROQ_TIMEOUT_SECONDS = 20`).
- **What the clinician sees:** At 20s the server gives up and returns; at 25s the
  browser aborts. The layering is deliberate — the server deadline sits *under*
  the client's so the server yields first and the client renders a real timeout
  rather than the request vanishing while work continues.
- **What could break it:** Voice upload is bounded at 120s
  (`VoiceCapture.tsx`), because 25s would abort a transcription that was going to
  succeed and a consult cannot be re-recorded. A hang there is visible for two
  minutes.

### 9. Provider returns 503 for an hour

- **Status:** PARTIAL
- **Location:** `frontend/lib/offline_summary.ts:81` (`deriveOfflineFindings` —
  threshold rules over stored values, with bidirectional negation handling);
  banner at `frontend/components/glance/TopCard.tsx:123`, wording
  *"Offline Mode (Rule-Derived) — Absence of a flag does not imply absence of
  clinical concern."*
- **Amended 3 Sep 2026 — the banner was dead code.** It was nested *inside*
  `{conflictCount > 0 && ...}`. When the AI is unreachable the contradiction
  check never runs, so `conflictCount` is 0, so the banner never rendered in
  the one scenario it exists for. An earlier draft of this audit cited its line
  number as evidence the control was present; the line existed and never
  executed. Hoisted to a sibling of the conflict card and pinned by
  `test_audit_boundaries.py::TestOfflineBannerIsNotGatedOnConflictCount`, which
  fails if it is ever re-nested. Verified in a browser with the AI service
  stopped and zero conflicts present.
- **What breaks first, still:** Trigger coverage. `conflictsDegraded` is set by
  the **contradiction** check failing, not by `/summarize` failing. A 503 on
  summarisation shows a toast and leaves the Glance View as it was. Fixing the
  nesting removed a defect; it did not widen the trigger surface, which remains
  one endpoint of four. Grade stays **PARTIAL**.
- **Why this shape:** An empty critical-flags panel reads as *"there are none"*;
  only a banner reads as *"this was not checked"*, and those are opposite
  clinical actions. The mechanism is right; its trigger surface is one endpoint
  of four.

### 10. Two clinicians edit the same note at 09:14

- **Status:** SURVIVES
- **Location:** CRDT path — Hocuspocus/Yjs merges character-level operations.
  Fallback path — `supabase/migrations/20260901000001_care_notes_version.sql:46`
  (`save_care_note_yjs`, compare-and-swap), called at
  `frontend/components/editor/CareNoteEditor.tsx:415`, banner at `:696`.
- **Database state at 09:15:** With collab up, both edits are merged — no lost
  update. With collab down (the realistic clinic case), the first save wins and
  the second is **refused**: the editor shows *"SAVE BLOCKED: Another user
  updated this note"* and **never clears the draft**, because that text is the
  only copy in existence.
- **What could break it:** OCC applies only on the fallback path by design; a
  refused save does not auto-retry with the fresh version, so recovery is manual.

### 11. Appointment link generated, never received

- **Status:** PARTIAL
- **Location:** `ai-service/services/messaging.py:68`
  (`confirmed_received` returns **False** for `sent`), `:139`
  (`resolve_telegram_chat`). Status advances only on a signed webhook.
- **The assumption that fails:** *"We sent it" is not "they received it."* The
  system now refuses to conflate them — but the deeper assumption is that the
  patient is reachable at all. **Telegram cannot message a phone number.** A bot
  may only send to a `chat_id`, which exists only after the patient opens the bot.
  An un-linked patient is genuinely unreachable and is reported as `queued`.
- **What breaks first:** With `MESSAGING_PROVIDER=mock` (the default), only the
  reserved range `+6580000001..4` is deliverable. **Nothing reaches a real
  patient today**, and the delivery record says so rather than claiming success.

### 12. Patient summary wrong by one dosage

- **Status:** SURVIVES
- **Location:** `ai-service/services/safety/patient_gate.py:152`
  (`screen_patient_draft`); enforced at
  `ai-service/routers/patient_message.py:198`; retraction at
  `ai-service/services/supabase_writer.py:229`.
- **Trace:** The **edited** text is screened at the moment of Send — not the AI's
  draft, because the clinician's edit is what the patient reads. Grounding
  sources are read **server-side**, so a fabricated dose cannot be supplied as its
  own grounding. Both care-team INSERT policies carry
  `AND visibility = 'internal'`, so a clinician's own token cannot create a
  patient-visible row: the gate cannot be skipped by not calling it.
- **Already-sent copy:** Retraction marks the original `is_retracted` (never
  deletes — the patient already read it) and posts a **new** patient-visible
  notice, because a flag on an old message only reaches someone who re-reads it.

### 12b. What the patient's own screen renders — amendment, 3 Sep 2026

- **Status:** was overclaimed; now PARTIAL
- **The finding:** scenario 12 grades the *send* gate SURVIVES, and it does. But
  a screenshot of the live patient portal showed two things the gate never
  covered, because neither is a send:
  1. The voice-capture result panel rendered clinician-grade telemetry to the
     patient — ASR confidence, entity counts, the `mock transcript` badge, and
     the full speaker-labelled transcript with `<PERSON_1>` placeholders.
  2. A care instruction reading `Lisinopril to 10 0000000mg daily` sat in the
     patient's Care Instructions card. Entry `47aaf426`, written **before** the
     maker-checker gate shipped on 31 Aug, carrying no approval verdict and
     rendered in the same type, in the same card, as a signed-off instruction.
- **The mistaken assumption, named:** that `visibility = 'patient_visible'`
  means approved. It never did. It means a row *may* be shown; it says nothing
  about whether a human vetted the words. Four such rows were reachable.
- **Fixed:** `frontend/lib/patient_visibility.ts` keys rendering on
  `metadata.patient_gate_verdict === 'passed'`, admitting retraction notices
  (no verdict by construction) and retracted entries (which must stay visible,
  struck through, or the patient holds a correction with nothing to attach it
  to). `VoiceCapture.tsx` gates its diagnostics on `isPatient`.
- **Found in passing, and worse than the leak:** the clinician timeline has
  rendered `[WITHDRAWN BY CARE TEAM]` since retraction shipped; the patient's
  own Care Instructions card **never did**. The one person who acted on a wrong
  dose was the one person not told it had been withdrawn. Now fixed.
- **What this is NOT:** a security control. The boundary that stops a patient
  reading unapproved clinical text is `AND visibility = 'internal'` on both
  care-team INSERT policies. A client-side predicate could never be that, and
  the module says so in its header so nobody later mistakes it for one.
- **Untested where it matters most.** These are pinned by structural assertions
  over source (`test_audit_boundaries.py::TestPatientViewShowsOnlyApprovedContent`)
  and seven unit tests on the rule itself. **ABSENT: any harness that renders
  the portal.** No automated test in this repo mounts the patient page, so what
  is proven is that the gate is in the code — not that a browser honours it.

### 13. Nurse says penicillin allergy; patient tells AI none

- **Status:** DOES NOT *(for the scenario exactly as stated)*
- **Location:** `ai-service/services/safety/clinical_conflict.py:373`
  (`detect_conflicts`), `:112` (`_ALLERGY` pattern); surfaced at
  `frontend/components/glance/SunshineBlock.tsx:152-154`.
- **Measured, not assumed.** Four phrasings run through the real engine:

  | Nurse entry | AI entry | Result |
  |---|---|---|
  | "allergic to penicillin" | "not allergic to penicillin" | **DETECTED** |
  | "Penicillin allergy documented" | "no known drug allergies" | **NOT DETECTED** |
  | "Allergy to penicillin documented" | "no known drug allergies" | **NOT DETECTED** |
  | "allergic to penicillin" | "no known drug allergies" | **NOT DETECTED** |

  The engine requires **both sides to name the same drug** with an explicit
  negation. A blanket denial does not register as contradicting a specific named
  allergen — and a blanket denial is exactly what a patient says.
- **What breaks first:** Nothing visible, which is the danger. The nurse's
  penicillin entry and the AI's "no known allergies" both sit in the timeline,
  the contradiction count reads **zero**, and the glance card shows no flag. A
  clinician scanning for conflicts is told there are none. `conflictsChecked`
  correctly distinguishes "none found" from "not checked" — but here the system
  genuinely believes it found none.
- **This was found by writing the test, not by reading the code.** An earlier
  draft of this audit graded it PARTIAL on the basis that the engine exists and
  ranks allergy highest. It does; it just does not fire on this input.
- **Build it better:** Add a `_BLANKET_DENIAL` pattern (`no known (drug )?
  allergies`, `NKDA`, `denies allergies`) and treat it as contradicting **any**
  positive allergy assertion on the same care note. That is a handful of lines
  and a new `ConflictClass` case — deliberately not written at 36 hours out
  without time to test it against real phrasing, because a half-tuned allergy
  detector that misses a different form is the same failure wearing a green tick.
  Pinned meanwhile by `tests/test_audit_boundaries.py::TestBlanketDenialIsNotDetected`.

### 14. Metric integrity — confidence score

- **Status:** SURVIVES
- **Location:** `ai-service/services/safety/confidence.py:52-54`
  (`0.50 × agreement + 0.35 × verification + 0.15 × rules`), `:59`
  (`ABSTAIN_THRESHOLD = 0.60`), `:150` (`assess_confidence`), `:217`
  (`apply_abstention`).
- **How we would know it was wrong:** Each component is independently
  inspectable and stored in `safety_metadata`. `importance_score`,
  `confidence_score` and `risk_level` are **separate columns** — rendering
  importance as confidence is the "decoration" failure, and the schema makes it
  impossible to do accidentally.
- **What the system does when it is wrong:** Below 0.60 the claim is withheld for
  review rather than guessed — *except* critical findings, which surface flagged,
  because silently withholding a possible anaphylaxis is the worse failure. Where
  confidence cannot be computed the UI shows **"not assessed"** and the badge
  renders **nothing** rather than defaulting to a band.
- **Not empirically calibrated.** The weights are a documented judgement, not
  validated against outcomes.

### 15. Learning loop, exposure bias and alert fatigue

- **Status:** PARTIAL
- **Location:** `ai-service/services/importance.py:42` (`ABSOLUTE_FLOOR`,
  critical ≥ 0.90), `:54` (`NO_DEMOTION_SEVERITIES` — learning may raise a `high`
  item, never push it below its unlearned value).
- **Alert fatigue:** Handled. `reject` carries −0.3, and the clinician most
  likely to dismiss the same alert forty times is a tired one at the end of a
  list — so the signal the loop would learn from is fatigue, and the item it
  would learn to hide is the alert that keeps firing. Forty dismissals now cannot
  bury an anaphylaxis flag. Tested.
- **Exposure bias: NOT handled.** The loop only ever scores what it surfaced, so
  it grows more confident about what it already believed and never learns that a
  suppressed item deserved promotion. **ABSENT: any random sampling of suppressed
  items.**
- **Build it better:** Surface a small random sample of withheld items per
  session, flagged as exploration, and score them separately. Not introduced
  untested into a ranking clinicians rely on.

### 16. Highlight cites a source that was edited

- **Status:** PARTIAL
- **Location:** `ai-service/services/provenance.py:178` (`verify_quote`, four
  verdicts: CURRENT / MODIFIED / SOURCE_DELETED / UNVERIFIABLE); badge at
  `frontend/components/glance/CriticalFlags.tsx:64`
  (`[SOURCE EDITED — VERIFY NOTE]`).
- **What happens:** The hash is authoritative and the version is a cheap
  pre-check; the **worse** of the two verdicts wins, never the better. A dose
  edited from `10mg` to `100mg` *within the same care-note version* is caught,
  which version comparison alone would miss. An edit to an unrelated sentence
  stays CURRENT — a tag that always fires is a tag nobody reads.
- **The gap:** Only the scribe path populates `exact_quote_hash`, so existing
  highlights have `source_note_version = NULL` and render "Source Modified".
  That is the safe direction — never a false claim of currency — but on seeded
  data the tag appears on everything, which erodes it.
- **ABSENT: side-by-side original vs current.** The badge marks staleness; it
  does not show what changed.

---

## Capabilities matrix

| # | Capability | Status | Anchor / absence |
|---|---|---|---|
| 1 | Streaming audio & noisy-environment ASR | **MISSING** | ABSENT: whole-file only, `routers/transcribe.py:153` |
| 2 | Speaker attribution & diarization | **PARTIALLY MET** | `services/transcription.py` diarizes; ABSENT: speaker-identity UX |
| 3 | Within-statement code-switching | **PARTIALLY MET** | `services/llm.py:45`; untested against real audio |
| 4 | Multilingual downstream processing | **MISSING** | Safety rules are English regex; `risk_rules.py` |
| 5 | Medical terminology & dosage confirmation | **MISSING** | ABSENT: formulary, interaction check, dose-range validation |
| 6 | Immutable version-bound provenance | **PARTIALLY MET** | `services/provenance.py:178`; scribe write path only |
| 7 | Extraction under negation & correction | **MET** | `services/safety/risk_rules.py:253`, bidirectional negation |
| 8 | Real-time collaboration without lost updates | **MET** | Yjs CRDT + OCC fallback, `20260901000001_care_notes_version.sql:46` |
| 9 | AI regeneration preserving human-confirmed state | **PARTIALLY MET** | `is_accepted` / `is_pinned` persist; ABSENT: explicit merge-on-regenerate |
| 10 | Contradictory human / patient / AI assertions | **MISSING** | `clinical_conflict.py:373` fires only on same-drug negation; blanket denial undetected |
| 11 | Audience-adapted readability | **MISSING** | Gated, deliberately not rewritten — see §12.3 of the brief |
| 12 | Self-learning: scoped, bounded, auditable, fatigue-resistant | **PARTIALLY MET** | `importance.py:42,54`; ABSENT: exposure-bias sampling |

**Four MET, five PARTIALLY MET, four MISSING** (capability 4 and 5 both missing;
counted once each). The three MISSING items are deliberate boundaries, documented
in `TECHNICAL_BRIEF.md` §12.3 with the reasoning for each — most importantly that
**the AI is not trusted to evaluate clinical safety**: grounding proves a dose was
said, never that it is correct.
