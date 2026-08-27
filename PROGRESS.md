# Nightingale — Build Scratchpad & Execution Plan

## Context

This is the working tracker for the Nightingale 72-Hour Build. The brief was delivered as a
PDF with no extractable text layer available locally (no poppler/pypdf, and plan mode forbids
installing). It was decoded by parsing the PDF's per-font `/ToUnicode` CMaps directly with
stdlib — all 6 pages recovered. Every requirement below is traced to that decoded text.

**Deadline: Friday 28 Aug 2026, 17:30 SGT. At time of writing: ~43 hours remain.**
Submit repo + brief + deliverables to `irakumar@ntngale.com`, cc `frank.ng@ntu.edu.sg`,
`carrene.teo@ntu.edu.sg`, subject `Nightingale 72HR Build — <Your Name>`.

Two phases are already complete and committed. The dominant remaining risks are (a) the test
suite does not currently execute, and (b) the P95 glance constraint is unmet and unmeasured —
both are directly scored.

On exiting plan mode this file gets copied to `PROGRESS.md` in the repo and kept updated as
work lands.

---

## Locked decisions

| Question | Decision |
|---|---|
| Test backend | **Ephemeral local Postgres.** Suites run against a throwaway cluster seeded from `001_foundation.sql`. No Docker, no cloud, no secrets — a grader runs `pytest` and it works. |
| Glance path | **SSR + single indexed `glance_cache` read + measurement harness.** Timeline decoupled and streamed separately. Report a real measured P95. |
| Bonus scope | **Ambient voice capture only.** Data decay is already built in the DB and only needs a paragraph in the brief. |
| Real-time | **Prove concurrency without the live WS server.** CRDT merge + deterministic same-section resolution tested directly; "Local Only" documented as graceful fallback. |

**Flagged interaction:** you scoped self-learning out of the bonus, but
`test_self_learning_importance.py` is one of the five required micro-tests. A passing test
forces a working loop, so a *minimal, test-driven* version is planned as required work — not
the full adaptive feature.

---

## Master checklist

Legend: `[x]` done & verified · `[~]` partial · `[ ]` not started

### 1. Shared "Care Note"
- [x] Unified single page per patient
- [x] Top/Glance card: content, open actions, critical risk flags
- [x] **Glance P95 ≤300ms** — **MEASURED: P95 79.7ms** (P50 68.4 / P99 96.5, n=100, 15 warmup discarded). 3.8× headroom.
- [x] Longitudinal timeline, time-ordered continuous feed
- [x] Entry types: patient/AI session summaries, AI-scribed consults, staff edits, clinician edits, system events
- [x] Per-entry metadata: `author_role`, `author_id`, `timestamp`, `type`, `provenance_pointer`
- [x] Threaded comments with resolve/unresolve
- [x] `@mentions`
- [x] Assignments — `ActionItems` now **rendered** in TopCard with working Assign/Done/Defer; deferring a critical/high finding requires a typed reason
- [x] Revision history: revert now records its version via `create_note_version` (the direct insert omitted the NOT NULL `version_number` and failed silently)
- [x] "View changes since X" — version-to-version compare in `VersionHistoryModal` + `SaveConfirmDialog`

### 2. AI Scribe & Prioritisation
- [x] Three interaction types (`ai_doctor_consult_summary`, `ai_nurse_consult_summary`, `ai_patient_session_summary`)
- [x] Distinct from manual notes; `author_role='system'`, `author_id=NULL`
- [x] `provenance_pointer` → `session_id`
- [x] Self-learning importance — loop closed, clinic-scoped, proven by test
- [x] Hard constraint: 1-click accept/reject on highlights
- [x] Each highlight shows `risk_reason` + provenance
- [x] Data decay — DB complete + documented in the technical brief

### 3. RBAC (Phase 1 ✅)
- [x] Server-side via PostgreSQL RLS; UI-only checks avoided
- [x] Patient cannot see internal comments or raw AI-scribed notes (type-level exclusion, defence in depth)
- [x] Staff/clinician cannot overwrite each other (`Authors can update their own entries`)
- [x] Clinic-scoped; cross-clinic denied (6 assertions)
- [x] Admin clinic-scoped oversight (read-only over WS)

### 4. Provenance & Trust
- [x] Click highlight → navigate to source entry
- [x] Trust badges with confidence
- [x] **Conflict resolution** — engine + UI badges with side-by-side verbatim quotes

### 5. Privacy & Security (Phase 2 ✅)
- [x] Presidio + spaCy `en_core_web_sm`
- [x] SG NRIC/FIN incl. 2022 M series; SG phones (6/8/9, `+65`, spacing); names incl. local conventions
- [x] Redaction strictly before any LLM call
- [x] Logs record counts/types, never PHI
- [x] All 5 AI endpoints require verified JWT; fail closed
- [x] Synthetic data only
- [x] TLS/at-rest — documented in the technical brief with a per-layer table

### 6. Micro-tests — **306 passing, 0 failing, no credentials required**
- [x] `test_rbac_scope` — 12, executing against real RLS
- [x] `test_revision_history` — 16, incl. non-tautological revert + metadata-only audit
- [x] `test_highlight_provenance` — 21 (12 offline schema + 9 DB)
- [x] `test_concurrent_edits` — 19, deterministic resolution + atomic versioning
- [x] `test_self_learning_importance` — 11, drives the real scorer end to end
- [x] `test_phi_redaction` — 41 zero-leakage assertions
- [x] `test_clinical_safety` — 64, the guardrails between the LLM and the record
- [x] `test_adversarial_safety` — 53, injection, obfuscation, multicultural PHI
- [x] `test_transcribe_endpoint` — 33, voice pipeline + credit guardrails + filing
- [x] `test_conflicts_endpoint` — 26, incl. titration false-positive suppression
- [x] `test_highlights_pipeline_safety` — 7, safety layer inside the real route
- [x] `test_meta_rls_sanity` — 3, guards against vacuous greens

### 7. Deliverables
- [x] Git repo with clear commit history (3 commits, descriptive)
- [x] README — rewritten: two-file env, redaction pipeline, RBAC as grants+policies
- [x] Technical Brief — architecture, schema, safety layer, measured P95, trade-offs
- [x] `ATTRIBUTION.txt` — Presidio/spaCy/PyJWT/cryptography/psycopg added with licences
- [x] `DEMO_SCRIPT.md` — 3 scenarios; conflict created live in Scenario B
- [ ] Demo video (Scenarios A/B/C)

### 8. Bonus
- [x] Ambient voice capture — mock-first, 120s cap, 5MB limit, two-key credit guardrail, 33 tests
- [x] Data decay — built, needs documenting

---

## Work plan

### Phase 3 — Make the tests real ✅ COMPLETE (commits c43d9f2, 7c8fd8e)
113 tests passing at that point, with zero credentials configured. *(Historical —
the suite is 306 today; see the checklist above.)*

- Build `tests/conftest.py` fixtures around an ephemeral Postgres: `initdb` → apply
  `supabase/migrations/001_foundation.sql` → shim `auth.users` + `auth.uid()` → seed via the
  8-arg `seed_demo_data`. Reuse the harness already proven in Phase 1 (24/24 RLS assertions);
  socket path must be short (macOS 103-byte limit).
- Role impersonation helper: `SET LOCAL ROLE authenticated` + `SET LOCAL request.jwt.claim.sub`.
  Must run as a **non-superuser** — superusers bypass RLS and every test passes vacuously.
- Port the 5 suites onto these fixtures. Keep assertions as written.
- `test_concurrent_edits`: two roles editing distinct sections merge non-destructively; add a
  deterministic same-section resolution loop (clinician precedence, else last-write-wins by
  timestamp with the loser preserved as a version).
- `test_revision_history`: verify increment, revert restores prior state, audit log is
  metadata-only (assert no PHI in `interaction_log`).
- `test_self_learning_importance`: close the minimal loop — pinning a highlight writes weighted
  `interaction_log` metadata; `compute_importance_score` reads it and scores semantically
  similar content higher on the next cycle. Scope `_compute_learned_score` by clinic (it
  currently ignores its `patient_id` argument and pools across tenants).

**Exit:** all 5 suites green with pasted output, on a machine with no credentials.

### Phase 4 — Glance View P95 ✅ MEASURED
- Convert `frontend/app/(dashboard)/patients/[id]/page.tsx` to a server component performing
  one indexed read of `care_notes.glance_cache`. Timeline, comments and highlights move to a
  separate streamed boundary (`<Suspense>`), so the card is not blocked by history reads.
- Keep the existing `TopCard`, `CriticalFlags`, `ActionItems` components — presentation is
  already correct; only the data path changes.
- Add server-timing instrumentation to the page and to AI entrypoints (`X-Process-Time`
  already exists in `ai-service/main.py` — mirror it in Next.js).
- Write `scripts/measure_glance.mjs`: N warm requests, report P50/P95/P99 and sample size, so
  the brief states a measured number rather than a claim.

**Exit:** measured P95 ≤300ms with method and N recorded.

### Phase 5 — Trust & conflict
- Implement programmatic clinician precedence: when an AI/patient entry conflicts with a
  clinician entry on the same section, the clinician entry wins and the conflict is surfaced
  with the existing `conflict` trust-badge variant.
- Verify revert end-to-end through `VersionHistoryModal` → `DiffViewer`.

### Phase 6 — Ambient voice capture (bonus)
- Patient view: `MediaRecorder` capture, PWA manifest, upload → transcription → **redact before
  LLM** → `ai_patient_session_summary` via the existing `/api/ai/scribe` endpoint.
- Clinical view: same path producing `ai_doctor_consult_summary` / `ai_nurse_consult_summary`
  with timestamps and confidence markers.
- Reuses Phase 2's redaction and provenance modules unchanged — no new PHI path.

### Phase 7 — Deliverables
- README: setup/run, where redaction happens, how RBAC is enforced. Correct the stale items
  the audit found (passwords, non-existent `.env.example`, and the now-*true* Presidio claim).
- Technical brief: architecture diagram, schema linkage
  (Entries ↔ Comments ↔ Versions ↔ Highlights ↔ Provenance ↔ AI_Scribed_Notes), trade-offs,
  measured P95, data-decay design.
- `ATTRIBUTION.txt`: add Presidio, spaCy, `en_core_web_sm`, PyJWT, cryptography + licences.
- `DEMO_SCRIPT.md` + record Scenarios A/B/C.

---

## Critical files

| Path | Role |
|---|---|
| `supabase/migrations/001_foundation.sql` | Single source of schema + RLS; test fixtures build from it |
| `ai-service/tests/conftest.py` | **Rewrite target** — ephemeral Postgres fixtures |
| `ai-service/services/importance.py` | Self-learning loop; `_compute_learned_score` needs clinic scoping |
| `frontend/app/(dashboard)/patients/[id]/page.tsx` | **Rewrite target** — client waterfall → SSR |
| `ai-service/services/redaction.py` | Reuse as-is for voice capture |
| `ai-service/services/provenance.py` | Reuse as-is — discriminated pointer builders |
| `ai-service/routers/scribe.py` | Voice capture posts here |
| `collab-server/persistence.ts` | Version-numbering race + `changed_by` uuid bug still open |

## Verification

```bash
# tests — must pass with no credentials configured
cd ai-service && .venv/bin/python -m pytest tests/ -v

# compilation
cd frontend && npx tsc --noEmit && cd ../collab-server && npx tsc --noEmit

# glance P95
npm run build && npm start && node scripts/measure_glance.mjs

# full stack
npm run dev   # fix dev:ai to use .venv/bin/uvicorn first
```

## Open risks — status as of 27 Aug 2026

### 1. Ambient voice capture — **CLOSED**
Built mock-first. `POST /api/ai/transcribe`, `VoiceCapture.tsx`, PWA manifest.
120-second hard stop, 5MB limit enforced before anything metered runs, and live
ElevenLabs calls gated behind TWO independent switches so no test can reach the
meter. 33 tests, zero credits spent. The `elevenlabs` SDK is an optional extra
imported lazily, so the suite runs with it absent.

### 1b. Original risk text (superseded)
Not implemented. No `MediaRecorder`, no PWA manifest, no transcription, no
speaker labelling. Everything downstream of it exists — redaction,
`/api/ai/scribe`, the three `ai_*` entry types, provenance — so it is a
UI-plus-transcription job rather than an architecture one, but it is still the
largest single remaining item in the brief.

Recommendation: it is a **bonus**, and the hard constraints (RBAC, redaction,
five micro-tests, P95) are all met and evidenced. Do not start it unless the
demo video is already recorded.

### 2. `createNoteVersion` race and `changed_by` sentinel — **CLOSED**
Verified, not assumed:
- The read-then-write is gone. `collab-server/persistence.ts` calls the
  `create_note_version()` RPC, which allocates `version_number` under
  `pg_advisory_xact_lock` inside one transaction.
- `changedByUserId` is `null` for system snapshots, never the string `"system"`.
  The two remaining `"system"` occurrences are a code comment and a log label.
- Covered by `TestAtomicVersionAllocation`: 10 concurrent threads produce
  distinct, contiguous version numbers, and a NULL author is accepted.

### 3. `app/api/patients/route.ts` hardcoded password — **CLOSED**
Every patient created through this route shared one guessable credential. It was
gated behind a clinician/admin session so it was never an authentication bypass,
but anyone who learned the string could sign in as any of them.

Now: a 32-byte random password that is never persisted, logged or returned, plus
a one-time `generateLink` recovery URL so the patient sets their own. The UI no
longer displays a password, because none exists to display.

### 4. Demo video / end-to-end stack — **CLOSED (unblocked)**
Credentials are in place and the full stack was verified live:
`/ready` reports all five checks true; a real ES256 clinician JWT authenticates
against the AI service; `/api/ai/conflicts` returns both contradictions
including the spelled-out `one hundred mg` normalised to `100mg`; redaction
returns `<PERSON_1>, NRIC <NRIC_1>, mobile <PHONE_1>`.

Recording is now purely a scheduling matter. `DEMO_SCRIPT.md` is written and its
Scenario B creates the contradiction live.

### 5. NEW — `source .env` corrupts the JWK
Found while verifying risk 4. `SUPABASE_JWT_JWK` holds a JSON document; the
shell strips its quotes on `source`, and because a real environment variable
takes precedence over the `.env` file, every AI endpoint then returns 503. Each
service parses `.env` itself — just start it and let it. Documented in the
README.

### 6. NEW — JWK **Set** handling was broken in both services
Also found verifying risk 4, and it had never run before because credentials did
not exist. Supabase publishes `{"keys":[...]}` and rotates within it; both
services passed the whole set where a single key was expected. Both now select
by the token's `kid`, reject an unmatched `kid` rather than guessing, and return
**401** for a malformed token instead of 500/503 — a bad credential is a client
error, not a service outage.

---

## Verification pass — 27 Aug 2026

Re-checked every item previously marked partial. Three had been over-claimed.

| Item | Claimed | Actual |
|---|---|---|
| Glance <10s + P95 ≤300ms | unmet | **P95 79.7 ms measured** (n=100). The <10s readability claim is a UX judgement, not a measured one — say so in the demo. |
| Assignments | "YES" (grep) | **Was dead code.** `ActionItems` was imported by `TopCard` and never rendered. Now rendered and wired. |
| Revision history revert | "verified" | **Was broken.** The version insert omitted `version_number` (NOT NULL, no default) so every revert failed `23502`, and the error was never checked — content reverted, audit trail silently gapped. Now uses the atomic RPC and surfaces failure. |
| "View changes since X" | not started | **Already existed** — version-to-version compare in `VersionHistoryModal`, plus `SaveConfirmDialog`. |
| Self-learning | partial | Closed: clinic-scoped, 11 tests drive the real scorer. |
| Data decay | undocumented | Closed: 3-tier design documented in the brief. |
| Conflict resolution | partial | Clinical contradictions: closed (server-side endpoint + UI). **Edit-precedence (`services/conflict.py`) remains a tested reference that nothing calls** — see below. |
| TLS/at-rest | not documented | Now documented per layer, including which controls this repo owns. |

### Known: `services/conflict.py` is not wired

The deterministic edit-precedence engine (role authority → recency → id) is
built and has 10 passing tests, but no caller. It resolves *same-section
concurrent edits*, which only arise on the Yjs/Hocuspocus path — and that path
runs in TypeScript, not Python, and degrades to "Local Only" without collab
secrets. So it is a correct, tested reference implementation for a code path
that is not currently live. Stated plainly rather than presented as shipped.

Clinical contradictions between authors are a different concern and **are**
live, at `POST /api/ai/conflicts`.

**Suite: 306 passing.** Full per-suite documentation in [TESTS.md](TESTS.md).

---

## Documentation audit — 27 Aug 2026

All project Markdown verified against the running code. Corrections:

- Test counts aligned to **306** across README, TECHNICAL_BRIEF, TESTS,
  DEMO_SCRIPT, DEMO_RUNBOOK and PROGRESS. Per-suite figures re-derived from
  `pytest --collect-only`, not copied forward.
- **TECHNICAL_BRIEF §3** gains *Patient data isolation at the Server Component
  boundary* — RLS lets a patient read their own `care_notes` row, which does not
  make `glance_cache.top_items` patient-facing.
- **§4.4** documents the three false-positive filters: same-author revision,
  deduplication keyed on **value** rather than `(author, value)`, and titration
  suppression by prescriber-value matching rather than dose ordering.
- **§4.7** documents server-side filing: why `author_id=NULL` makes the write
  impossible from any browser session, and the four checks re-applied by hand
  because the service-role key bypasses RLS.
- **§4.8** is new: `jsonb` snapshots holding real note text, additive revert, and
  why the old revert test was tautological.
- Role tables now distinguish care-note editing (clinician-write) from timeline
  notes, comments and action handling (staff and clinician).

`USER_GUIDE.md` does not exist and was never created — the user-manual document
was dropped in favour of fixing the five audit findings. `CLAUDE.md` and
`guardrails.md` retain their historical "40 tests error at fixture setup"
references **deliberately**: those describe the inherited starting state that
motivated the guardrails, not the current suite.

---

## 28 Aug 2026 — patient data isolation, corrected

The previous entry recorded patient isolation as solved at the Server Component
boundary. It was not. Probing the **live** deployment with a real patient session
returned the clinician's assessment straight from PostgREST:

```
GET /rest/v1/care_notes?select=glance_cache   (patient's own JWT)
  {"text": "eGFR declining: 62 → 45 over 6 months",
   "risk_level": "critical", "confidence": 0.92}
  {"text": "Cardiology referral pending since Jan 15", "status": "unresolved"}
```

RLS is row-level, not column-level. `glance_cache` sits on a row the patient owns
and must be able to read, so the whole column came back. Filtering it in the page
hid it from the page; it never withheld it from the patient. The portal looked
correct the entire time.

**What changed**

- `care_note_assessments` — new table holding `top_items` and
  `changes_since_last_visit`, with three care-team policies and **no patient
  policy**. Not a filter a patient fails: no rule exists that could admit them.
- `001_foundation.sql` seeds the two halves separately; `glance_cache` keeps only
  `care_plan_score` and `last_visit`.
- `/patients/[id]` fetches the assessment only for clinician/staff/admin and
  recomposes it into `glance_cache` in memory, so components are untouched.
- `fix_live_grants.sql` carries the same change to a deployed database, back-
  filling before stripping so a part-way failure cannot destroy the assessment.
- `TECHNICAL_BRIEF.md` §3 rewritten — the old text claimed the component boundary
  was enforcement rather than UI hiding, which the probe disproved.

**Verification is against the API, not the page**, since a UI test would have
passed throughout. Six assertions in `test_rbac_scope.py` plus
`scripts/verify_patient_isolation.mjs` for live deployments. Both match on the
*shape* of a clinical judgement — severity band, confidence, triage status — not
on clinical words: a patient's own care plan legitimately reads "Consider
nephrology consult if eGFR continues to decline", and a keyword scan flags that
wrongly. The live verifier was confirmed non-vacuous: it reports 6 failures
against the unfixed deployment.

### Two steps that need a human

1. **Local suite is blocked by the machine, not the code.** SysV shared memory is
   exhausted host-wide — even a 56-byte `shmget` returns ENOMEM with zero
   segments allocated, leaked accounting from the many ephemeral clusters this
   session. `initdb` cannot run, so the 207 DB-backed tests cannot execute.
   Clear it with `sudo sysctl -w kern.sysv.shmall=65536 kern.sysv.shmmax=16777216`
   or a reboot. The 105 offline tests (redaction, clinical safety) pass.

2. **The live database still leaks** until `supabase/fix_live_grants.sql` is run
   in the Supabase SQL editor. DDL is not reachable through PostgREST and no
   database password or CLI token is present in the repo. **Apply the SQL before
   deploying the frontend** — the new page reads `care_note_assessments`, so a
   frontend deployed first would show clinicians an empty Top Card.

**Test count held at 306 deliberately.** 312 now collect, but only 105 have been
verified in this session; the docs will be updated to a measured number once the
full suite can run again. An unverified count is exactly what the accuracy audit
was for.
