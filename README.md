# Nightingale — Shared Longitudinal Patient Note

A single shared care note per patient, collaborative across clinician, staff and
patient, augmented by AI that is not permitted to be trusted on its own.

The interesting part of this build is not the LLM. It is the layer that treats
the LLM as fallible: verbatim extraction instead of generation, deterministic
risk floors the model cannot lower, measured confidence with an abstention rule,
and a maker-checker firewall on anything a patient will read.

**257 automated tests, all runnable offline with no credentials.**

---

## Quick start

### Prerequisites

| | Version | Notes |
|---|---|---|
| Node | 20+ | 24.x used in development |
| Python | 3.11+ | 3.14 used in development |
| PostgreSQL client binaries | 14+ | `initdb`, `pg_ctl` — **required to run the test suite**, which builds its own throwaway database. `brew install postgresql@14` |

### 1. Install

```bash
npm install                          # workspaces: frontend + collab-server

cd ai-service
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m spacy download en_core_web_sm   # PHI redaction model
cd ..
```

`en_core_web_sm` is not a pip dependency and must be downloaded explicitly. The
AI service refuses to report ready without it.

### 2. Run the tests — no configuration needed

```bash
cd ai-service && .venv/bin/python -m pytest tests/ -v
```

The suites build an ephemeral PostgreSQL cluster, apply
`supabase/migrations/001_foundation.sql` verbatim, and seed both demo clinics.
Nothing is mocked and no cloud project is involved, so the RLS policies under
test are exactly the ones that deploy.

### 3. Environment — two files, not one

This trips people up. The repo-root `.env` is loaded explicitly by the AI
service (`main.py`) and the collab server (`index.ts`). **Next.js reads env from
its own project root**, so `frontend/.env.local` is required and independent.
Neither substitutes for the other; a key needed by both is written to both.

```bash
cp .env.demo .env        # then fill in the values below
```

**`.env` (repo root)** — AI service and collab server:

| Key | Required for |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` / `SUPABASE_URL` | everything |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | auth |
| `SUPABASE_SERVICE_ROLE_KEY` | AI scribe ingestion, collab server, seeding |
| `SUPABASE_JWT_JWK` *or* `SUPABASE_JWT_SECRET` | verifying JWTs on AI endpoints and the WebSocket |
| `GROQ_API_KEY` | summarisation and highlight extraction |
| `TEST_*_EMAIL` / `TEST_*_PASSWORD` | test fixtures and the latency harness |

**`frontend/.env.local`** — Next.js:

```
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...          # server-only; never NEXT_PUBLIC
NEXT_PUBLIC_AI_SERVICE_URL=http://localhost:8000
NEXT_PUBLIC_COLLAB_URL=ws://localhost:1234
```

> **A blank value is worse than a missing one.** `os.getenv(key, default)`
> returns `""` for `KEY=`, defeating every fallback. Comment the line out instead.

> **Do not `source .env` before starting a service.** `SUPABASE_JWT_JWK` holds a
> JSON document, and the shell strips its quotes on `source`, exporting a corrupt
> value. Because a real environment variable takes precedence over the `.env`
> file, every AI endpoint then returns 503 with
> `SUPABASE_JWT_JWK is not valid JSON`. Each service loads `.env` itself with a
> proper parser — just start it and let it. If you must export by hand,
> single-quote the JWK value.

### 4. Database

Apply `supabase/migrations/001_foundation.sql` — one file expressing the
intended final state, replacing a historical 001–014 chain in which later
migrations silently reverted earlier ones.

If the schema is already deployed but PostgREST returns
`42501 permission denied for table`, the deployment predates the grants block:
run `supabase/fix_live_grants.sql` in the SQL Editor. It is idempotent and ends
with a verification query.

```bash
./scripts/seed.sh      # creates 8 auth users, seeds both clinics
```

### 5. Run

```bash
npm run dev            # frontend :3000, collab :1234, AI :8000
```

> `npm run dev:ai` invokes a bare `uvicorn`, which resolves to a system Python
> without FastAPI. Point it at `.venv/bin/uvicorn` or activate the venv first.

Without `SUPABASE_JWT_SECRET`/`SUPABASE_JWT_JWK` the collab server exits and the
editor degrades to **"Local Only"** — an amber indicator, with edits saving
directly to Supabase. This is a designed fallback, not a failure.

### Demo accounts

All use `demo-password-123` (written by `scripts/seed.sh`, which is
authoritative — earlier docs listed values that were never created).

| Clinic | Role | Email |
|---|---|---|
| Nightingale Family | Clinician | `clinician@nightingale.demo` |
| Nightingale Family | Staff | `staff@nightingale.demo` |
| Nightingale Family | Patient | `patient@nightingale.demo` |
| Nightingale Family | Admin | `admin@nightingale.demo` |
| Sunrise Medical | Clinician | `dr.miller@sunrise.demo` |
| Sunrise Medical | Staff | `emma.wilson@sunrise.demo` |
| Sunrise Medical | Patient | `robert.lee@sunrise.demo` |
| Sunrise Medical | Admin | `michael.brown@sunrise.demo` |

Two clinics exist so cross-tenant denial is testable rather than asserted.

---

## Where redaction happens

**`ai-service/services/redaction.py`**, and it runs *strictly before* any text
reaches Groq. Every AI router follows the same order, with no path around it:

```
raw clinical text
  └─> redact()                    services/redaction.py
        ├── Presidio AnalyzerEngine + spaCy en_core_web_sm   (PERSON, LOCATION, …)
        ├── custom SG PatternRecognizers                     (NRIC/FIN, phones, MRN)
        ├── title + role-label recognizers                   (local naming conventions)
        └── caller-supplied deny-list                        (the patient's own name)
  └─> placeholders  <PERSON_1> <NRIC_1> <PHONE_1> …
  └─> Groq LLM                    ← only ever sees this form
  └─> validate_and_repair_placeholders()   ← integrity check before restoration
  └─> de_redact()                 server-side map, never sent to a client
  └─> assert_no_residual_placeholders()    ← nothing raw reaches the record
```

**Singapore-specific recognisers.** Off-the-shelf Presidio misses all of these:

| Entity | Pattern | Notes |
|---|---|---|
| NRIC / FIN | `[STFGM]\d{7}[A-Z]` | includes the **M series** introduced in 2022 |
| SG phone | `(?:\+65[\s-]?)?[689]\d{3}[\s-]?\d{4}` | mobile (8/9), landline (6), optional `+65`, tolerates `9123 4567` |
| MRN | `MRN[:\s#-]?\d{6,10}` | |
| Titled names | `Dr\.? Sarah Chen`, `Mdm Nurul Aisyah binte Rahman` | case-sensitive; `binte\|binti\|bin` ordered longest-first |
| Labelled names | `Patient: Tan Ah Kow` | anchored on the role label |

`en_core_web_sm` is trained on US/EU news text and routinely misses Chinese,
Malay and Tamil name forms, which is why layers 3 and 4 exist. The scribe
endpoint passes the patient's own `display_name` as an exact-match deny-list
entry, so coverage of the single most sensitive identifier does not depend on
model recall.

**Redaction is an accuracy control, not only a privacy one.** Over-redaction
destroys the clinical signal the summary exists to carry, so:

- a clinical allow-list stops medication names (`Lisinopril`, `Metformin`) being
  redacted as PERSON — spaCy tags them as people;
- `DATE_TIME` is deliberately **not** redacted, because clinical reasoning
  depends on relative timing ("eGFR fell over 6 months") and dates alone are low
  re-identification risk once names, NRIC, phone and MRN are gone;
- negations and dosages are never caught in the PII net — asserted by test.

**Logs record counts and entity types, never PHI:**

```
Redacted 5 entities (MRN:1, NRIC:1, PERSON:2, PHONE:1) from 119 chars
```

**Verification:** `tests/test_phi_redaction.py` — 33 assertions, each written as
"this string must NOT appear in the text we would send to Groq". Covers 5 NRIC
series, 6 phone formats, 5 name forms including local conventions, and asserts
clinical values survive untouched.

---

## How RBAC is enforced

**At the database, in `supabase/migrations/001_foundation.sql`.** PostgreSQL Row
Level Security is the enforcement point; the UI adapts to role but is never the
control. Every one of the eight tables has RLS enabled.

### Two layers, both required

**Grants** decide whether a role may touch a table at all. **RLS policies**
decide which rows it sees. `anon` receives schema usage only — every policy
requires `auth.uid()`, which is NULL for an anonymous caller.

### Clinic scoping via SECURITY DEFINER helpers

A nested `EXISTS` on an RLS-protected table re-evaluates that table's RLS and
raises `42501`. All scoping therefore runs through `SECURITY DEFINER` functions
with a pinned `search_path`:

| Helper | Answers | Used by |
|---|---|---|
| `get_user_clinic_id()` | caller's tenant | all clinic-scoped policies |
| `get_user_role()` | caller's role, as text | all role checks |
| `check_care_note_access()` | clinic membership **only** | care-team policies |
| `check_patient_owns_care_note()` | ownership | **patient policies only** |

The last distinction is load-bearing. `check_care_note_access()` returns true for
*every* note in the caller's clinic. Used in a patient policy it would expose
other patients' records — a leak the historical chain actually carried. Patient
policies scope on ownership, never on clinic.

### What each role can do

| | Patient | Staff | Clinician | Admin |
|---|---|---|---|---|
| Own care note | ✅ | — | — | — |
| Clinic care notes | ❌ | ✅ | ✅ | ✅ |
| Internal timeline entries | ❌ | ✅ | ✅ | ✅ |
| Raw AI-scribed notes | ❌ | ✅ | ✅ | ✅ |
| Internal comments | ❌ | ✅ | ✅ | ✅ |
| Highlights | ❌ | ✅ | ✅ | ✅ |
| Version history | ❌ | ✅ | ✅ | ✅ |
| Own `patient_message` readback | ✅ | ✅ | ✅ | ✅ |
| Edit another role's entry | ❌ | ❌ | ❌ | ❌ |
| Archived entries | ❌ | ❌ | ✅ | ✅ |
| Real-time editing (WebSocket) | ❌ rejected | ✅ write | ✅ write | 👁 read-only |

**Patients cannot read raw AI-scribed notes** — enforced twice. The entry is
`visibility = 'internal'`, *and* the patient SELECT policy excludes
`ai_doctor_consult_summary`, `ai_nurse_consult_summary` and
`ai_patient_session_summary` by type. If one were mis-marked patient-visible it
would still be hidden.

**Staff and clinicians cannot overwrite each other.** The only UPDATE policy on
`timeline_entries` is `author_id = auth.uid()`. A cross-role write does not fail
in the UI — it changes no row.

### Where RLS is bypassed, and what replaces it

The service-role key bypasses RLS. It is used in exactly three places, each of
which re-implements the tenant and role checks by hand:

| Site | Why | Replacement check |
|---|---|---|
| `ai-service/services/supabase_writer.py` | AI-scribed entries need `author_role='system'`, `author_id=NULL`, which no user JWT can satisfy | `resolve_care_note()` compares the caller's clinic before any write |
| `collab-server/persistence.ts` | Yjs state load/save | role allowlist **then** clinic match; patients rejected before the note is even looked up |
| `frontend/app/api/patients/route.ts` | account provisioning | session + clinician/admin gate |

### Verification

```bash
cd ai-service && .venv/bin/python -m pytest tests/test_rbac_scope.py tests/test_meta_rls_sanity.py -v
```

`test_meta_rls_sanity.py` guards against a green suite that proves nothing: it
asserts the same query returns different row counts per role.

```
patient sees       1     clinician sees     8
sunrise clinician  3     service role      11   (RLS bypassed)
```

Tests run as the non-superuser `authenticated` role — a superuser bypasses RLS,
which would make every access-control assertion pass vacuously.

---

## Architecture

```
Browser (Next.js 15, App Router)
  ├── HTTPS ──→ Supabase        Auth · Postgres · RLS · Realtime
  ├── WSS ────→ Hocuspocus      Yjs CRDT sync, JWT + role gate    :1234
  └── HTTPS ──→ FastAPI         redaction → safety layer → Groq   :8000
```

| Service | Path | Port |
|---|---|---|
| Frontend | `frontend/` | 3000 |
| Collab server | `collab-server/` | 1234 |
| AI service | `ai-service/` | 8000 |

Full design rationale, failure modes and measured latency are in
[TECHNICAL_BRIEF.md](TECHNICAL_BRIEF.md).

## Testing

```bash
cd ai-service && .venv/bin/python -m pytest tests/ -v   # 257 tests
cd frontend && npx tsc --noEmit
cd collab-server && npx tsc --noEmit
node scripts/measure_glance.mjs                          # glance P95
```

| Suite | Tests | Covers |
|---|---|---|
| `test_rbac_scope.py` | 12 | role isolation, cross-clinic denial, cross-role writes |
| `test_revision_history.py` | 14 | version increment, revert, metadata-only audit |
| `test_highlight_provenance.py` | 21 | pointer schema, span resolution, referential integrity |
| `test_concurrent_edits.py` | 19 | non-destructive merge, deterministic resolution, atomic versioning |
| `test_self_learning_importance.py` | 11 | learning loop moves scores; tenant isolation |
| `test_phi_redaction.py` | 33 | zero PHI leakage; no over-redaction |
| `test_clinical_safety.py` | 64 | extraction, risk floors, confidence, conflicts, patient gate, feedback |
| `test_meta_rls_sanity.py` | 3 | guards against vacuous greens |
| `test_highlights_pipeline_safety.py` | 7 | safety layer runs inside the real `/api/ai/highlights` route |
| `test_adversarial_safety.py` | 53 | prompt injection, obfuscated contradictions, multicultural PHI, RLS probes |
| `test_conflicts_endpoint.py` | 20 | `/api/ai/conflicts`, JWK-set selection, auth failure modes |

## Licence

MIT. Third-party libraries and models are listed in
[ATTRIBUTION.txt](ATTRIBUTION.txt).
