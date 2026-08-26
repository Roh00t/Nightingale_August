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

## 7. Traps

- `.env.demo` ships every value **blank**. Blank is worse than absent: it defeats the
  `os.getenv(key, default)` fallbacks in `conftest.py`, so tests fail with
  `supabase_url is required` instead of trying localhost.
- Seed passwords are `demo-password-123` (from `scripts/seed.sh`). The README's
  `demo-clinician-2026` values do not exist. Three files disagree; the script is authoritative.
- npm workspaces hoist to the root `node_modules`. Empty `frontend/node_modules` is correct.
- `README.md` references `.env.example` and `supabase/seed.sql`. Neither exists.
- The versions table is `note_versions`. The brief calls it `versions`. See §3.
