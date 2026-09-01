# Nightingale — Shared Longitudinal Patient Note

A single shared care note per patient, collaborative across clinician, staff and
patient, augmented by AI that is **not permitted to be trusted on its own**.

The interesting part of this build is not the LLM. It is the layer that treats
the LLM as fallible: verbatim extraction instead of generation, deterministic
risk floors the model cannot lower, measured confidence with an abstention rule,
and a maker-checker firewall on anything a patient will read.

**398 automated tests · Glance P95 79.7 ms · suite runs offline with no credentials.**

> **Compliance posture — read this before quoting the security sections.**
> This is a prototype built on **synthetic data only**. The design is
> HIPAA/PDPA-*informed* — PHI is redacted before it leaves the process, access is
> enforced at the database rather than the UI, and audit records carry counts and
> identifiers rather than clinical content. It is **not a compliance
> attestation**. Handling real patient data would additionally require, at
> minimum: BAAs or equivalent with Groq, ElevenLabs, Supabase and Railway;
> defined audit-log retention and review; encryption-at-rest key custody; formal
> access reviews; and a breach-notification process. None of that exists here,
> and the code says so where it matters rather than implying otherwise.

---

## Live deployment

### **https://nightingale-august-frontend-6ktv.vercel.app**

Sign in with any account from the [demo accounts table](#demo-accounts) —
password `demo-password-123`. Start as `clinician@nightingale.demo`, then open a
second window as `patient@nightingale.demo` to see the same record from both
sides.

| Tier | Host | Notes |
|---|---|---|
| Frontend — Next.js 15 App Router | **Vercel** | `regions: ["sin1"]` |
| Database — PostgreSQL, RLS on 11 tables | **Supabase** | managed; RLS is the enforcement point |
| AI service — FastAPI | **Railway** | Nixpacks build, `uvicorn` via `Procfile` |
| Collab — Hocuspocus / Yjs | **not deployed** | degrades to "Local Only"; see below |

**Why the collab server is not deployed.** Hocuspocus holds a stateful WebSocket
per editing session with the authoritative Y.Doc in memory. Serverless functions
are short-lived and share no memory between invocations, so a CRDT authority
cannot live there. Rather than fake it, the client detects the absent server,
shows an amber **"Local Only"** badge, and falls back to reading and writing
`yjs_state` directly against Supabase. Edits persist and survive reload; what is
lost is live cursors and simultaneous co-editing.

---

## Core architecture

```
Browser
  │
  ├── HTTPS ──→ Next.js 15 (App Router)          Vercel
  │               Server Components · RSC · route handlers
  │
  ├── HTTPS ──→ Supabase                          managed
  │               Auth (GoTrue, ES256) · PostgreSQL · RLS · Realtime
  │
  ├── HTTPS ──→ FastAPI                           Railway
  │               redaction → safety layer → Groq
  │               ├── Microsoft Presidio + spaCy en_core_web_sm (English only)
  │               ├── Groq  openai/gpt-oss-20b
  │               ├── ElevenLabs Scribe v2 (metered, off by default)
  │               └── python-multipart (audio ingestion)
  │
  └── WSS ────→ Hocuspocus                        local only
                  Yjs CRDT · JWT + role gate
```

### CORS — production and preview origins

The AI service is called directly from the browser, so it owns the CORS
decision; **no Vercel setting affects it**. Configuration lives in
`ai-service/main.py`.

```python
DEFAULT_ALLOWED_ORIGINS = [
    "https://nightingale-august-frontend-6ktv.vercel.app",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    ...
]
VERCEL_PREVIEW_ORIGIN_REGEX = (
    r"https://nightingale-august-frontend-6ktv(-[a-z0-9-]+)?\.vercel\.app"
)
```

Preview deployments get a generated hostname per branch, so they cannot be
enumerated in advance and need a pattern. **That pattern is deliberately
narrower than the obvious one.** `*.vercel.app` is a *shared* namespace — anyone
with a Vercel account can claim a free subdomain — so
`...6ktv.*\.vercel\.app` would grant CORS to whoever registers
`nightingale-august-frontend-6ktvevil`, and because `.*` spans dots it would also
admit `...6ktv.attacker.vercel.app`. Both were measured as matching the loose
form. The shipped pattern requires a literal hyphen before any suffix, excludes
dots from the suffix class, and escapes the dots in `.vercel.app`. Starlette
applies it with `fullmatch`, which is what makes `...vercel.app.evil.com` fail.

`allow_origins` is never `*`: combined with `allow_credentials=True` browsers
reject it outright, and this service is called with an `Authorization` header.
`CORS_ORIGINS` overrides the list and **replaces** rather than extends it, so the
list can be narrowed.

**A failed preflight does not look like a configuration problem.** Starlette
answers an unlisted origin with `400` and *no* `Access-Control-Allow-Origin`
header, so the browser reports a generic CORS failure and the request never
leaves. Diagnose it directly:

```bash
curl -s -o /dev/null -D - -X OPTIONS \
  https://nightingaleaugust-3zme-production.up.railway.app/api/ai/summarize \
  -H "Origin: https://nightingale-august-frontend-6ktv.vercel.app" \
  -H "Access-Control-Request-Method: POST" | grep -i access-control-allow-origin
```

---

## Clinical security & data flow

### PHI redaction — strictly ordered, and structurally enforced

Redaction runs in `ai-service/services/redaction.py` **before any Groq call**,
never after. The ordering used to be guaranteed only by code reading —
`redact()` on one line, the model call twenty lines later — and code reading does
not survive refactoring: a new endpoint or an added `patient_context=` argument
reintroduces the leak with no test failing.

So the guarantee is now structural. Every model call funnels through one
chokepoint (`_call_with_retry`), and `services/egress_guard.py` re-derives the
answer from the payload immediately before the network call:

```
redact()  →  safety layer  →  assert_safe_for_model(messages)  →  Groq
                                        │
                                        └─ raises UnredactedEgressError
```

It **refuses rather than repairs**. Silently fixing one leaked field would hide
the call path that skipped redaction, and the next unredacted field would go out
under a different shape. What it guarantees is narrow and worth stating exactly:
**no NRIC/FIN or Singapore phone number reaches Groq, whatever the call path
did.** Free-text names remain Presidio's job, upstream.

**Detection layers**, in order:

| Layer | Covers |
|---|---|
| spaCy `en_core_web_sm` via Presidio | general PERSON / LOCATION / ORG |
| Custom SG `PatternRecognizer`s | NRIC/FIN incl. the 2022 **M** series, `+65` phones, MRN |
| Local name conventions | `bin` / `binte` / `s/o` / `d/o` / `a/l` / `a/p`, CJK names, titled and labelled forms |

Presidio is pinned to English recognizers only — `AnalyzerEngine(...,
supported_languages=["en"])` — so no other language pipeline is constructed at
startup. The full pattern table and entity coverage are in
[Redaction reference](#redaction-reference--patterns-and-entity-coverage).

**Logs are scrubbed independently.** `services/log_scrubbing.py` attaches a
filter to the **root** logger at import time, before the app is constructed, so
uvicorn access lines and third-party tracebacks are covered too — those are the
records most likely to carry raw input, and a filter on the application logger
alone would miss both. It is regex-only by design: model inference on the
emitting thread would add latency to every record and can deadlock if the
analyser itself logs.

### The maker-checker firewall

Patient-facing generation is the highest-severity path in the system and the only
one where AI output cannot reach its audience unaccompanied.

1. **Grounding (deterministic).** Every clinical token — drug names, doses,
   numbers with units — must appear in the source record. Compared by **set
   membership, not substring**: substring matching would treat `1` as grounded by
   `10mg` and `10` as grounded by `100mg`, which is a dosing error passing in the
   dangerous direction.
2. **Prohibited speech acts (deterministic).** Diagnosis, prognosis,
   stop-treatment, dose-change, emergency-deferral. These are clinician speech
   acts; an assistant has no standing to make them.
3. **Named human approval,** recorded and rendered as visible attribution.

Three properties make this enforcement rather than decoration:

- The **edited** text is screened at the moment of Send. The AI's draft may have
  been grounded; what the patient reads is the clinician's edit of it.
- Grounding sources are read **server-side** from the record. If the caller
  supplied them, a fabricated dose could be sent as its own grounding and verify
  against itself — the request model deliberately has no `sources` field.
- The write happens only on the **passing branch of the same call**. Both
  care-team INSERT policies carry `AND visibility = 'internal'`, so a clinician's
  own token in curl can write internal notes and **cannot create a
  patient-visible row at all**.

Approval cannot rescue a blocked draft; the checks run first. A blocked draft
returns `422` with the offending tokens, rendered as chips beside the draft.

### Multi-tenant isolation

RLS on all tables, with clinic scoping through `SECURITY DEFINER` helpers rather
than a JWT claim. `get_user_clinic_id()` reads `profiles.clinic_id`, which is
authoritative immediately — a JWT claim is a snapshot that keeps granting access
to a clinic a user was removed from until their token expires.

Defence in depth: every patient-scoped table also carries a denormalised
`clinic_id`, filled and locked by trigger so a caller cannot forge it, and
checked by a **RESTRICTIVE** policy. Restrictive matters — Postgres ORs
permissive policies together, so a second permissive policy would have *widened*
access.

**RLS is row-level, not column-level.** That is not a footnote; it is the single
most expensive lesson in this codebase. A patient-readable row exposes every
column in it, so the clinical assessment lives in `care_note_assessments` (no
patient policy at all) rather than in a field of the note. A trigger additionally
strips `top_items` and `changes_since_last_visit` from `care_notes.glance_cache`
on every write, because the display object that carries them for clinicians is
also a write source, and stripping in application code has to be remembered at
every call site.

### API resilience

| Guardrail | Value | Rationale |
|---|---|---|
| Groq per-attempt timeout | **20 s** | The SDK defaults to none; a stalled upstream held an async worker until the connection dropped elsewhere |
| Browser `AbortController` | **25 s** | Sits *above* the server deadline so the server gives up first and the client renders a real timeout |
| Voice upload | **120 s** | 25 s would abort work that was going to succeed, and a consult cannot be re-recorded |
| Groq SDK retries | **0** | Retry lives in one place; otherwise attempts multiply and a rate-limited key hangs for minutes |

All `/api/ai/*` endpoints are **POST-only** — other methods return `405`,
verified — and every one requires a verified JWT and fails closed.

**Optimistic concurrency on care notes.** `care_notes.version` plus
`save_care_note_yjs()` compare-and-swap, applied **only on the fallback path**.
With Hocuspocus connected, Yjs merges character-level operations and OCC would
reject merges the CRDT resolves correctly. The window is when collab is *down*:
the editor writes the whole document with a plain UPDATE, two clinicians both
write, and the second silently erases the first. A refused save does **not**
retry with the fresh version — that is precisely the clobber it exists to
prevent. It stops, keeps the text on screen, and says the note was not written,
because the dangerous property is that unsaved text looks saved.

---

## Quick start

### Prerequisites

| | Version | Needed for |
|---|---|---|
| Node | 20+ | frontend, collab server |
| Python | 3.11+ | AI service |
| PostgreSQL **client binaries** | 14+ | **the test suite**, which builds its own throwaway database — `brew install postgresql@14` |

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
AI service will not report ready without it.

### 2. Run the tests — no configuration needed

```bash
cd ai-service && .venv/bin/python -m pytest tests/ -v
```

Expect **398 passed**. The suites build an ephemeral PostgreSQL cluster, apply
`supabase/migrations/001_foundation.sql` verbatim, and seed both demo clinics.
No cloud project, no Docker, no credentials, and no metered API calls.

Full per-suite documentation is in **[TESTS.md](TESTS.md)**.

### 3. Environment — two files, not one

This trips people up. The repo-root `.env` is loaded explicitly by the AI
service (`main.py`) and the collab server (`index.ts`). **Next.js reads env from
its own project root**, so `frontend/.env.local` is required and independent.

```bash
cp .env.demo .env        # then fill in the values below
```

**`.env` (repo root)** — AI service and collab server:

| Key | Needed for |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` / `SUPABASE_URL` | everything |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | auth |
| `SUPABASE_SERVICE_ROLE_KEY` | AI scribe ingestion, collab server, seeding |
| `SUPABASE_JWT_JWK` *or* `SUPABASE_JWT_SECRET` | verifying JWTs on AI endpoints and the WebSocket |
| `GROQ_API_KEY` | summarisation and highlight extraction |
| `ELEVENLABS_API_KEY` + `ELEVENLABS_LIVE_ENABLED` | live voice transcription (**optional**, metered) |
| `TEST_*_EMAIL` / `TEST_*_PASSWORD` | the latency harness |

**`frontend/.env.local`** — Next.js, for local development:

```
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...          # server-only; never NEXT_PUBLIC
NEXT_PUBLIC_AI_SERVICE_URL=http://localhost:8000
NEXT_PUBLIC_COLLAB_URL=ws://localhost:1234
```

### Production environment

Three hosts, three sets of variables. They are not interchangeable.

**Vercel** (frontend). `NEXT_PUBLIC_*` values are **inlined into the client
bundle at build time**, so editing one in the dashboard changes nothing until you
redeploy:

| Key | Value |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | the Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | the anon/publishable key |
| `SUPABASE_SERVICE_ROLE_KEY` | server-only; used by `/api/patients` and patient-login |
| `NEXT_PUBLIC_AI_SERVICE_URL` | `https://nightingaleaugust-3zme-production.up.railway.app` |
| `NEXT_PUBLIC_COLLAB_URL` | omit in production — absent means the "Local Only" fallback |

**Railway** (AI service):

| Key | Needed for |
|---|---|
| `SUPABASE_URL` | Supabase REST base for scribe ingestion |
| `SUPABASE_SERVICE_ROLE_KEY` | writing `author_role='system'` entries |
| `SUPABASE_JWT_JWK` *or* `SUPABASE_JWT_SECRET` | **verifying JWTs — without it every endpoint returns 503** |
| `GROQ_API_KEY` | summarisation and highlight extraction |
| `ELEVENLABS_API_KEY` | ambient voice transcription |
| `ELEVENLABS_LIVE_ENABLED` | `true` to spend credits; unset means mock transcripts |

Readiness is self-reporting — `GET /ready` returns each check by name:

```bash
curl -s https://nightingaleaugust-3zme-production.up.railway.app/ready
```

```json
{"status":"ready","checks":{"groq_api_key":true,"supabase_url":true,
 "supabase_service_key":true,"jwt_verification":true,"redaction_engine":true}}
```

`jwt_verification: false` is the one that silently breaks every AI feature: the
service still reports `ready` (redaction and Groq are the critical checks) and
still answers `/health`, but every authenticated call returns
`503 Authentication is not configured on this service`. It fails closed, which is
the right posture — but check this field, not just the status string.

**Supabase** (database) needs no application variables. Apply
`supabase/fix_live_grants.sql` once to an existing deployment; a fresh project
gets everything from `supabase/migrations/001_foundation.sql`.

> **Three env traps, each of which cost real debugging time:**
>
> **A blank value is worse than a missing one.** `os.getenv(key, default)`
> returns `""` for `KEY=`, defeating every fallback. Comment the line out instead.
>
> **`SUPABASE_JWT_JWK` must be a single, valid, properly escaped JSON object.**
> This is the highest-frequency deployment failure in this project. The variable
> holds a JWK Set — `{"keys":[{"kty":"EC","alg":"ES256","kid":"...",...}]}` — and
> anything that mangles it produces the same symptom: **every AI endpoint returns
> `503 Authentication is not configured on this service`**, while `/health`
> continues to answer `200` and `/ready` still reports `status: ready`.
>
> That combination is what makes it expensive. The service looks alive from every
> angle a load balancer checks, so the failure presents as "the AI is broken"
> rather than "one variable is malformed". Diagnose it by reading the *field*,
> not the status line:
>
> ```bash
> curl -s https://<your-railway-host>/ready
> # {"status":"ready","checks":{...,"jwt_verification":false,...}}
> #                                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^ this one
> ```
>
> `jwt_verification: false` means the value is absent or unparseable. The three
> ways it gets mangled:
>
> | Cause | What happens |
> |---|---|
> | `source .env` before starting | The shell strips the outer quotes, exporting `{keys:[...]}`. A real env var beats the `.env` file, so the service reads the corrupt one. **Never source it** — each service parses `.env` itself. |
> | Pasting into a dashboard field with newlines | Railway/Vercel preserve the newline; `json.loads` fails. Paste it as one line. |
> | Double-escaping | `\"` inside an already-quoted dashboard value yields `\\"` in the process. Paste raw JSON, not a shell-escaped string. |
>
> Verify locally before deploying:
>
> ```bash
> python3 -c "import json,os; json.loads(os.environ['SUPABASE_JWT_JWK']); print('valid')"
> ```
>
> Fetch a fresh copy from `https://<project>.supabase.co/auth/v1/.well-known/jwks.json`.
> `SUPABASE_JWT_SECRET` (symmetric HS256) is accepted as a fallback, but the
> project publishes ES256 keys and Supabase is migrating off symmetric secrets —
> prefer the JWK.

### 4. Database

Apply `supabase/migrations/001_foundation.sql` — one file expressing the
intended final state, replacing a historical 001–014 chain in which later
migrations silently reverted earlier ones.

If the schema is already deployed but PostgREST returns
`42501 permission denied for table`, that deployment predates the grants block.
Run **`supabase/fix_live_grants.sql`** in the Supabase SQL Editor. It is
idempotent, adds the clinical-safety columns, and creates `care_note_assessments`
— backfilling the clinical assessment out of `glance_cache` before stripping it,
so a part-way failure cannot destroy it. Audit the result with
**`supabase/verify_grants.sql`**, which should show `true` across all nine
tables.

```bash
./scripts/seed.sh      # creates 8 auth users, seeds both clinics
```

### 5. Run

```bash
npm run dev            # frontend :3000, collab :1234, AI :8000
```

> **`npm run dev:ai` invokes a bare `uvicorn`**, which resolves to a system
> Python without FastAPI. Either activate the venv first, or start the AI
> service directly:
>
> ```bash
> cd ai-service && .venv/bin/uvicorn main:app --reload --port 8000
> ```

Without `SUPABASE_JWT_SECRET`/`SUPABASE_JWT_JWK` the collab server exits and the
editor degrades to **"Local Only"** — an amber indicator, with edits saving
directly to Supabase. This is a designed fallback, not a failure.

### Demo accounts
<a id="demo-accounts"></a>

All use `demo-password-123` (written by `scripts/seed.sh`, which is
authoritative).

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

## Troubleshooting

Every one of these was hit during development. Check here first.

| Symptom | Cause | Fix |
|---|---|---|
| `42501 permission denied for table` on every request | Schema deployed without role grants | Run `supabase/fix_live_grants.sql` |
| AI endpoints all return **503** `SUPABASE_JWT_JWK is not valid JSON` | You ran `source .env`; the shell mangled the JSON | Start services without sourcing; each loads `.env` itself |
| AI endpoints return **401** with a valid-looking token | Signing key rotated, or the JWK has no matching `kid` | Refresh `SUPABASE_JWT_JWK` from the Supabase dashboard |
| `npm run dev:ai` → `ModuleNotFoundError: fastapi` | Bare `uvicorn` resolved to system Python | Use `.venv/bin/uvicorn` |
| `pytest` → `Form data requires "python-multipart"` | Missing upload dependency | `.venv/bin/pip install python-multipart` |
| `pytest` → suites skip with "PostgreSQL binaries not on PATH" | No `initdb`/`pg_ctl` | `brew install postgresql@14` and add to PATH |
| Tests fail `supabase_url is required` | Stale `conftest` expectations | Should not happen — the suites build their own DB; re-pull |
| `/ready` shows `redaction_engine: false` | spaCy model missing | `.venv/bin/python -m spacy download en_core_web_sm` |
| Editor shows amber **"Local Only"** | No collab JWT secret | Expected without secrets; not a failure |
| Login fails for every account | Not seeded, or wrong password | `./scripts/seed.sh`; all accounts use `demo-password-123` |
| Voice capture returns **413** | Recording over 5 MB | Recordings are hard-capped at 120 s; re-record shorter |
| Voice summary says **"mock transcript"** | Live transcription not enabled | Intended default — see below |

**Health check first, always:**

```bash
curl -s localhost:8000/ready | python3 -m json.tool
```

All five checks should read `true`. `redaction_engine` and `groq_api_key` are
the two that gate readiness.

---

## API reference

All `/api/ai/*` endpoints require a verified Supabase JWT
(`Authorization: Bearer <token>`) and **fail closed** — an unconfigured service
returns 503 rather than accepting unverified callers.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/ai/summarize` | Structured clinical summary from timeline entries |
| `POST` | `/api/ai/highlights` | Risk-scored highlights, through the full safety layer |
| `POST` | `/api/ai/redact` | PHI redaction (counts returned; the map never leaves the server) |
| `POST` | `/api/ai/scribe` | AI-scribed consult ingestion → system-authored timeline entry |
| `POST` | `/api/ai/conflicts` | Cross-author clinical contradiction detection |
| `POST` | `/api/ai/transcribe` | Ambient voice → diarized transcript → redaction → summary |
| `GET` | `/health` · `/ready` | Liveness and readiness |
| `POST` | `/api/patients` | Create a patient (clinician/admin only, one-time setup link) |
| `POST` | `/api/auth/patient-login` | Patient account lookup (clinician/admin only, clinic-scoped) |

Interactive docs at `http://localhost:8000/docs` once the service is running.

---

## Ambient voice capture

Record a consult in the browser; the audio is transcribed with speaker labels,
PHI is stripped, and a structured clinical summary is written back to the
timeline as a system-authored entry.

```
MediaRecorder (120 s hard cap)
  -> POST /api/ai/transcribe        ≤5 MB enforced before anything metered runs
  -> ElevenLabs Scribe v2           diarized  — OR a deterministic mock
  -> services/redaction.py          PHI stripped BEFORE any LLM sees the text
  -> structuring LLM                redacted, speaker-labelled dialogue only
  -> ai_*_consult_summary entry     author_role='system', author_id=NULL
```

**Transcription is metered, and by default this feature spends nothing.** A live
call needs two independent switches:

| Switch | Where |
|---|---|
| `?live=true` | on the request |
| `ELEVENLABS_LIVE_ENABLED=true` | on the deployment |

One is not enough by design — a stray query parameter in a fixture or a copied
curl command would otherwise be sufficient to start spending credits, and the
failure is silent until the balance is gone. With either switch off, the
endpoint returns a deterministic mock transcript, which is what the entire test
suite runs against.

The `elevenlabs` SDK is an **optional** dependency, imported lazily inside the
live branch only:

```bash
cd ai-service && .venv/bin/pip install elevenlabs     # only for live transcription
```

Capture mode follows the caller's role and is enforced server-side: patients may
only produce `patient_session` captures, staff `nurse_consult`, clinicians
`doctor_consult`. The component needs a **secure origin** — `localhost` counts;
a LAN IP does not, so `getUserMedia` will be refused over plain HTTP.

---

## Redaction reference — patterns and entity coverage

**`ai-service/services/redaction.py`**, strictly before any text reaches Groq.
Every AI router follows the same order, with no path around it:

```
raw clinical text
  └─> redact()                    services/redaction.py
        ├── Presidio + spaCy en_core_web_sm   (PERSON, LOCATION, …)
        ├── custom SG PatternRecognizers      (NRIC/FIN, phones, MRN)
        ├── title + role-label recognizers    (local naming conventions)
        ├── structured-payload recognizers    (JSON / key=value)
        └── caller-supplied deny-list         (the patient's own name)
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
| SG phone | `(?:\+65[\s-]?)?[689]\d{3}[\s-]?\d{4}` | mobile, landline, `+65`, tolerates `9123 4567` |
| MRN | `MRN[:\s#-]?\d{6,10}` | |
| Titled names | `Dr. J Tan`, `Mdm Nurul Aisyah binte Rahman` | initials and particles handled |
| Labelled names | `Patient: Tan Ah Kow` | anchored on the role label |
| Structured | `{"patient_name":"…"}`, `name=…` | NER sees a quoted token, not a sentence |
| CJK names | 2–4 ideographs | `en_core_web_sm` has no coverage |

**Redaction is an accuracy control, not only a privacy one.** Over-redaction
destroys the clinical signal the summary exists to carry, so a clinical
allow-list stops medication names being redacted as PERSON; `DATE_TIME` is
deliberately **not** redacted because clinical reasoning depends on relative
timing; `IC`/`NRIC`/`FIN` survive as labels while the number beside them is
removed; and negations are never stripped — asserted by test.

**Logs record counts and entity types, never PHI:**

```
Redacted 5 entities (MRN:1, NRIC:1, PERSON:2, PHONE:1) from 119 chars
```

---

## How RBAC is enforced

**At the database, in `supabase/migrations/001_foundation.sql`.** PostgreSQL Row
Level Security is the enforcement point; the UI adapts to role but is never the
control. All eleven tables have RLS enabled.

**Grants** decide whether a role may touch a table at all; **policies** decide
which rows it sees. `anon` receives schema usage only — every policy requires
`auth.uid()`, which is NULL for an anonymous caller.

Clinic scoping runs through `SECURITY DEFINER` helpers with a pinned
`search_path`, because a nested `EXISTS` on an RLS-protected table re-evaluates
that table's RLS and raises `42501`:

| Helper | Answers | Used by |
|---|---|---|
| `get_user_clinic_id()` | caller's tenant | all clinic-scoped policies |
| `get_user_role()` | caller's role, as text | all role checks |
| `check_care_note_access()` | clinic membership **only** | care-team policies |
| `check_patient_owns_care_note()` | ownership | **patient policies only** |

The last distinction is load-bearing. `check_care_note_access()` returns true for
*every* note in the caller's clinic; used in a patient policy it would expose
other patients' records — a leak the historical migration chain actually carried.

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
| Edit the shared care note | ❌ | 👁 read-only | ✅ | 👁 read-only |
| Add timeline notes / comments | ❌ | ✅ | ✅ | ❌ |
| Assign / defer open actions | ❌ | ✅ | ✅ | ❌ |
| Resolve contradictions, revert versions | ❌ | ❌ | ✅ | ❌ |
| Real-time editing (WebSocket) | ❌ rejected | ✅ write | ✅ write | 👁 read-only |
| Voice capture mode | `patient_session` | `nurse_consult` | `doctor_consult` | `doctor_consult` |

**Patients cannot read raw AI-scribed notes** — enforced twice. The entry is
`visibility='internal'`, *and* the patient SELECT policy excludes the three
`ai_*` entry types by name. A mis-marked entry would still be hidden.

**Staff and clinicians cannot overwrite each other.** The only UPDATE policy on
`timeline_entries` is `author_id = auth.uid()`. A cross-role write does not fail
in the UI — it changes no row.

**Edit rights on the shared care note.** The intended rule is that the note body
is the clinician's section: clinicians write it, staff contribute through timeline
entries and comments instead. The editor enforces that in the UI, showing staff
and admins a read-only surface with a stated reason ("the care note is the
clinician's section — add a staff note or comment below instead") rather than a
silently inert field.

Be precise about *where* that is enforced, because it is not RLS. The UPDATE
policy on `care_notes` admits `clinician`, `staff` and `admin`:

```sql
CREATE POLICY "Care team can update care notes"
  ON care_notes FOR UPDATE
  USING (clinic_id = get_user_clinic_id()
         AND get_user_role() IN ('clinician', 'staff', 'admin'));
```

That is deliberate, and it is the row-vs-column problem again. The note body
(`yjs_state`) and the care plan (`glance_cache`) are **columns of the same row**,
and staff genuinely own care-plan work — ticking off plan items is theirs to do.
A policy is row-level, so it cannot grant one column and withhold the other.

| Path | Gate | Staff can write the note body? |
|---|---|---|
| Browser → PostgREST | RLS `Care team can update care notes` | yes — RLS cannot separate the columns |
| Browser → Hocuspocus → service-role | `COLLAB_WRITE_ROLES` in `collab-server/persistence.ts` | yes — `["clinician", "staff"]` |
| Editor UI | read-only surface for staff/admin | no |

So today the clinician-only rule is a **UI convention**, not an enforced
boundary. Closing it properly means splitting the note body out of the row the
care plan lives in — the same fix already applied to the clinical assessment (see
below) — or narrowing `COLLAB_WRITE_ROLES` to `["clinician"]` and adding a
column-scoped trigger. Documented as open rather than overstated.

**Patients never receive the internal clinical assessment** — and this one *is*
structurally enforced. The assessment lives in `care_note_assessments`, a table
with three care-team policies and no patient policy, so a patient gets zero rows
by any route including a direct API call with their own token. It previously lived
in `care_notes.glance_cache`, where filtering it in the server component hid it
from the page while leaving it readable — see
[TECHNICAL_BRIEF.md §3](TECHNICAL_BRIEF.md).

### Where RLS is bypassed, and what replaces it

The service-role key bypasses RLS. It is used in exactly three places, each of
which re-implements the tenant and role checks by hand:

| Site | Why | Replacement check |
|---|---|---|
| `ai-service/services/supabase_writer.py` | AI-scribed entries need `author_role='system'`, `author_id=NULL`, which no user JWT can satisfy | `resolve_care_note()` compares the caller's clinic before any write |
| `collab-server/persistence.ts` | Yjs state load/save | role allowlist **then** clinic match; patients rejected before the note is looked up |
| `frontend/app/api/patients/route.ts` | account provisioning | session + clinician/admin gate |

### Verification

```bash
cd ai-service && .venv/bin/python -m pytest \
  tests/test_rbac_scope.py tests/test_meta_rls_sanity.py -v
```

`test_meta_rls_sanity.py` guards against a green suite that proves nothing: it
asserts the same query returns *different* row counts per role.

```
patient sees       1     clinician sees     8
sunrise clinician  3     service role      11   (RLS bypassed)
```

Tests run as the non-superuser `authenticated` role — a superuser bypasses RLS,
which would make every access-control assertion pass vacuously.

**Against a live deployment.** The suite proves the policies; it does not prove
what got deployed. Two checks close that gap.

```bash
node scripts/verify_patient_isolation.mjs
```

Signs in as a real patient and a real clinician and asserts against the **API**,
not the rendered page — a UI test would have passed for the entire period the
assessment was exposed. Nine assertions: the patient gets zero rows from
`care_note_assessments` by table and by `care_note_id`, no severity or confidence
grading survives in `glance_cache`, no high/critical timeline entry is reachable,
and — as the control — clinician and staff access is **intact**, so the checks
cannot be satisfied by breaking the care team instead.

It matches on the *shape* of a clinical judgement (`risk_level`, `confidence`,
`status`) rather than on clinical words. A patient's own care plan legitimately
reads "Consider nephrology consult if eGFR continues to decline" — that is an
instruction written for them, and a keyword scan flags it wrongly.

```
All checks passed — the assessment is unreachable by the patient.
```

`supabase/verify_grants.sql`, run in the Supabase SQL editor, audits table-level
grants and RLS state for every table in `public`, enumerated from `pg_class`:

| table_name | authenticated_select | authenticated_insert | service_role_select | rls_enabled | policies |
|---|---|---|---|---|---|
| care_note_assessments | true | true | true | true | 3 |
| care_notes | true | true | true | true | 4 |
| timeline_entries | true | true | true | true | 7 |
| *…eleven tables* | | | | | |

Enumerated rather than listed by hand on purpose: a fixed `IN (...)` list silently
omits tables added later, and the omission renders identically to a pass — which
is exactly how `care_note_assessments` went unchecked after it was added.

Read the two together. A grant is the table-level door; RLS decides rows.
`authenticated` holding SELECT on `care_note_assessments` is correct and expected
— the patient still gets nothing, because no policy admits them. Only the API
probe answers row visibility.

---

## Service map

| Service | Path | Production | Local |
|---|---|---|---|
| Frontend | `frontend/` | Vercel — [nightingale-august-frontend-6ktv.vercel.app](https://nightingale-august-frontend-6ktv.vercel.app) | `npm run dev:frontend` :3000 |
| Database | `supabase/` | Supabase managed Postgres | same project |
| AI service | `ai-service/` | Railway — [nightingaleaugust-3zme-production.up.railway.app](https://nightingaleaugust-3zme-production.up.railway.app) | `.venv/bin/uvicorn main:app --reload --port 8000` |
| Collab server | `collab-server/` | **not deployed** — degrades to "Local Only" | `npm run dev:collab` :1234 |

Full design rationale, failure modes and measured latency are in
**[TECHNICAL_BRIEF.md](TECHNICAL_BRIEF.md)**.

---

## Deployment

Three hosts, three build paths. Nothing here is inferred — each was verified
against the running deployment.

### Frontend — Vercel

`vercel.json` at the repo root:

```json
{
  "buildCommand": "cd frontend && npm run build",
  "installCommand": "cd frontend && npm install",
  "outputDirectory": "frontend/.next",
  "framework": "nextjs",
  "regions": ["sin1"]
}
```

`sin1` (Singapore) because the Supabase project and the clinical users are both
in-region; the Glance View's P95 budget is 300 ms and a trans-Pacific round trip
spends most of it.

**`NEXT_PUBLIC_*` is inlined into the client bundle at build time.** Editing one
in the Vercel dashboard changes nothing until you redeploy — this catches people
out with `NEXT_PUBLIC_AI_SERVICE_URL` in particular.

### AI service — Railway

Built with **Nixpacks**, not Docker. `ai-service/railway.json` sets the builder
and start command; `ai-service/Procfile` carries `web: uvicorn main:app --host
0.0.0.0 --port $PORT`.

**The spaCy model is not a pip dependency.** `en_core_web_sm` is distributed as a
wheel outside PyPI, so `pip install -r requirements.txt` does not bring it, and
the redaction engine will not start without it. It must be installed during the
**build**, not at runtime — a first-request download is a multi-second cold start
on the PHI path, and a network failure there fails the request that most needs to
succeed. Either add a build step:

```bash
python -m spacy download en_core_web_sm
```

or pin the wheel directly in `requirements.txt` so it installs with everything
else:

```
en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl
```

The second is preferable — it makes the model a declared dependency rather than
an out-of-band step someone can forget. Confirm either way from
`/ready` → `redaction_engine: true`.

**CORS lives here, not on Vercel.** A change to `allow_origins` requires a
**Railway** redeploy; pushing the frontend has no effect on it.

### Database — Supabase

Apply `supabase/DEPLOY_20260901_all.sql` in the SQL Editor — four migrations in
dependency order, verified to apply and re-apply cleanly against a throwaway
cluster. Then confirm:

```bash
node scripts/verify_patient_isolation.mjs   # 9 assertions, all must pass
```

Full manual sequence, including Realtime replication and the per-host variable
split, is in **[DEPLOY_CHECKLIST.md](DEPLOY_CHECKLIST.md)**.

### Post-deploy verification

```bash
curl -s https://<railway-host>/ready
```

Read the individual checks, not `status`. The service reports `ready` when Groq
and redaction are healthy, so `jwt_verification: false` passes the status line
while breaking every authenticated endpoint.

---

## Testing

```bash
npm test                     # 398 pytest tests (uses ai-service/.venv)
npm run typecheck            # tsc --noEmit across frontend + collab-server
npm run build                # Next.js production build
node scripts/measure_glance.mjs --n 100    # glance P95, needs the app running
```

Or directly:

```bash
cd ai-service && .venv/bin/python -m pytest tests/ -v
cd frontend && npx tsc --noEmit
cd collab-server && npx tsc --noEmit
```

> There is **no frontend unit-test suite.** Vitest and Testing Library are
> installed but no `.test.tsx` files exist, so the previous `test:frontend`
> script always exited 1; it has been removed rather than left as a command that
> cannot pass. Frontend correctness is covered by `tsc --noEmit`, the production
> build, and the backend suites that exercise the same API contracts.

| Suite | Tests | Covers |
|---|---|---|
| `test_clinical_safety.py` | 64 | extraction, floors, abstention, conflicts, patient gate, feedback |
| `test_adversarial_safety.py` | 53 | prompt injection, obfuscated contradictions, multicultural PHI, RLS probes |
| `test_phi_redaction.py` | 41 | zero PHI leakage; clinical values preserved |
| `test_transcribe_endpoint.py` | 33 | ambient voice pipeline, payload limits, credit guardrails, server-side filing |
| `test_conflicts_endpoint.py` | 26 | contradiction detection, titration suppression, JWK-set selection, auth failure modes |
| `test_highlight_provenance.py` | 21 | pointer schema, span resolution, referential integrity |
| `test_concurrent_edits.py` | 19 | non-destructive merge, deterministic resolution, atomic versioning |
| `test_revision_history.py` | 16 | version increment, non-tautological revert, metadata-only audit |
| `test_rbac_scope.py` | 12 | role isolation, cross-clinic denial, cross-role writes |
| `test_self_learning_importance.py` | 11 | learning moves scores; tenant isolation holds |
| `test_highlights_pipeline_safety.py` | 7 | the safety layer runs *inside* the real route |
| `test_meta_rls_sanity.py` | 3 | guards against a green suite that proves nothing |

Full per-suite documentation, including every defect these tests found, is in
**[TESTS.md](TESTS.md)**. Build progress and open risks are in
**[PROGRESS.md](PROGRESS.md)**. Demo narrative in **[DEMO_SCRIPT.md](DEMO_SCRIPT.md)**;
exact startup and live-run commands in **[DEMO_RUNBOOK.md](DEMO_RUNBOOK.md)**.

## Licence

MIT. Third-party libraries and models are listed in
[ATTRIBUTION.txt](ATTRIBUTION.txt).
