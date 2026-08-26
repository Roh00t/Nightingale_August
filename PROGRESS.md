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
- [ ] **Glance readable+actionable <10s AND P95 ≤300ms warm** ← unmet, scored
- [x] Longitudinal timeline, time-ordered continuous feed
- [x] Entry types: patient/AI session summaries, AI-scribed consults, staff edits, clinician edits, system events
- [x] Per-entry metadata: `author_role`, `author_id`, `timestamp`, `type`, `provenance_pointer`
- [x] Threaded comments with resolve/unresolve
- [x] `@mentions`
- [ ] Assignments ("Assign to staff") — brief marks optional; deprioritised
- [x] Revision history: revert verified additive & auditable; version numbering now atomic
- [ ] "View changes since X"

### 2. AI Scribe & Prioritisation
- [x] Three interaction types (`ai_doctor_consult_summary`, `ai_nurse_consult_summary`, `ai_patient_session_summary`)
- [x] Distinct from manual notes; `author_role='system'`, `author_id=NULL`
- [x] `provenance_pointer` → `session_id`
- [x] Self-learning importance — loop closed, clinic-scoped, proven by test
- [x] Hard constraint: 1-click accept/reject on highlights
- [x] Each highlight shows `risk_reason` + provenance
- [~] Data decay — DB complete, undocumented

### 3. RBAC (Phase 1 ✅)
- [x] Server-side via PostgreSQL RLS; UI-only checks avoided
- [x] Patient cannot see internal comments or raw AI-scribed notes (type-level exclusion, defence in depth)
- [x] Staff/clinician cannot overwrite each other (`Authors can update their own entries`)
- [x] Clinic-scoped; cross-clinic denied (6 assertions)
- [x] Admin clinic-scoped oversight (read-only over WS)

### 4. Provenance & Trust
- [x] Click highlight → navigate to source entry
- [x] Trust badges with confidence
- [~] **Conflict resolution** — deterministic engine done + tested; **UI surfacing pending**

### 5. Privacy & Security (Phase 2 ✅)
- [x] Presidio + spaCy `en_core_web_sm`
- [x] SG NRIC/FIN incl. 2022 M series; SG phones (6/8/9, `+65`, spacing); names incl. local conventions
- [x] Redaction strictly before any LLM call
- [x] Logs record counts/types, never PHI
- [x] All 5 AI endpoints require verified JWT; fail closed
- [x] Synthetic data only
- [ ] TLS/at-rest — document (Supabase-managed)

### 6. Micro-tests — **113 passing, 0 failing, no credentials required**
- [x] `test_rbac_scope` — 12, executing against real RLS
- [x] `test_revision_history` — 14, incl. revert + metadata-only audit assertions
- [x] `test_highlight_provenance` — 21 (12 offline schema + 9 DB)
- [x] `test_concurrent_edits` — 29, incl. deterministic resolution + atomic versioning
- [x] `test_self_learning_importance` — 11, drives the real scorer end to end
- [x] `test_phi_redaction` — 33 zero-leakage assertions
- [x] `test_meta_rls_sanity` — guards against vacuous greens

### 7. Deliverables
- [x] Git repo with clear commit history (3 commits, descriptive)
- [ ] README: setup/run, **where redaction happens**, **how RBAC is enforced** — stale
- [ ] 2–3 page Technical Brief: architecture diagram, full schema linkage, trade-offs
- [x] `ATTRIBUTION.txt` — needs Presidio/spaCy/PyJWT added
- [ ] `DEMO_SCRIPT.md` — currently 1 byte
- [ ] Demo video (Scenarios A/B/C)

### 8. Bonus
- [ ] Ambient voice capture (selected)
- [x] Data decay — built, needs documenting

---

## Work plan

### Phase 3 — Make the tests real ✅ COMPLETE (commits c43d9f2, next)
113 tests passing with zero credentials configured.

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

### Phase 4 — Glance View P95
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

## Open risks

1. **Time.** Ambient voice capture is the largest item in the brief. If Phase 3/4 overrun, it
   is the first thing to cut — the hard constraints outscore it.
2. ~~`createNoteVersion` race + `changed_by` sentinel~~ — **FIXED.** Numbering moved into
   `create_note_version()` under a per-care-note advisory lock; `changed_by` now passes NULL.
   Proven by a 10-thread concurrent allocation test.
3. **`app/api/patients/route.ts`** still mints new patient accounts with a hardcoded password.
   Gated behind clinician/admin auth so not a bypass, but should be closed before submission.
4. **Demo video** needs a working end-to-end stack, which needs real Supabase credentials for
   the hosted demo. Local Postgres covers tests but not the recorded demo.
