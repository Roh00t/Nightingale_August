# Nightingale

Nightingale is a shared longitudinal patient-note system for care teams. It brings a
patient's history, current care plan, important changes, and team discussion into one
workspace instead of scattering context across visit-specific free-text notes.

The project is designed around three ideas:

- **One longitudinal record:** a patient has one care note and a time-ordered clinical timeline.
- **Trust made visible:** entries and AI suggestions retain role, provenance, risk, and review state.
- **Least-privilege access:** clinic tenancy and user role are enforced in the database and at service boundaries.

> **Status:** This is a development and demonstration project. It is not a medical device and must not be used with real patient data.

## Live Demo

https://frontend-lake-five-85.vercel.app

The hosted demo contains synthetic data for two separate clinics. The seed script is the
source of truth for demo credentials; all seeded accounts use `demo-password-123`.

| Clinic | Example accounts |
| --- | --- |
| Nightingale Family Clinic | `clinician@nightingale.demo`, `staff@nightingale.demo`, `patient@nightingale.demo`, `admin@nightingale.demo` |
| Sunrise Medical Center | `dr.miller@sunrise.demo`, `emma.wilson@sunrise.demo`, `robert.lee@sunrise.demo`, `michael.brown@sunrise.demo` |

## Architecture

```text
Browser / Next.js 15
  ├── HTTPS ──> Supabase Auth, Postgres, RLS, and Realtime
  ├── WSS  ──> Hocuspocus collaboration server :1234
  └── HTTPS ──> FastAPI AI service :8000
                    ├── Presidio + spaCy PHI detection
                    └── Groq clinical language-model operations
```

| Service | Stack | Responsibility |
| --- | --- | --- |
| `frontend/` | Next.js 15, React 19, TipTap, Yjs | Authentication, role-aware screens, care-note editing, timeline, glance view, comments, and patient messaging |
| `collab-server/` | Hocuspocus, Yjs, TypeScript | JWT-authenticated WebSocket synchronization, persistence, and collaborative note state |
| `ai-service/` | FastAPI, Presidio, spaCy, Groq, Python | PHI redaction, summarization, highlight extraction, scribe ingestion, and importance scoring |
| `supabase/` | PostgreSQL, Auth, RLS | Identity, clinic-scoped records, audit data, realtime-backed persistence, and demo fixtures |

The frontend can fall back to **Local Only** editing when the collaboration server is not
configured. In that mode, edits are saved through the application's direct Supabase path;
real-time presence and multi-user synchronization are unavailable.

## Product Areas

### Glance view

The patient view surfaces critical flags, open actions, care-plan completeness, recent
changes, and important highlights before the user opens the full note. The database stores a
denormalized `care_notes.glance_cache` and updates its timestamp when timeline entries or
highlights change.

### Longitudinal timeline

Timeline entries support manual notes, patient messages, instructions, administrative events,
and distinct AI summaries for doctor, nurse, and patient sessions. Each entry can carry
visibility, risk level, author role, metadata, and a provenance pointer.

### Collaboration and review

Clinicians and staff can work in the same TipTap document with Yjs synchronization. The UI
also includes threaded comments, mentions, resolve states, version history, diff viewing, and
review flows for saving selected changes and accepting or rejecting highlights.

### AI-assisted care

The AI service exposes separate operations for summarization, highlight extraction, patient
messages, redaction, and scribe ingestion. Importance scoring uses clinician interactions such
as pin, accept, reject, dismiss, and view to improve future suggestions.

AI output remains visibly distinct from clinician-authored content and includes risk and
provenance information. AI suggestions are support tools and require human review.

## Privacy and Access Control

Nightingale uses defense in depth:

1. **Supabase Row Level Security:** every core table is protected by policies for `patient`,
   `staff`, `clinician`, and `admin` roles.
2. **Tenant isolation:** staff access is scoped to their clinic; patients are scoped to their
   own care note and patient-visible timeline entries.
3. **Service-boundary checks:** the collaboration service repeats the clinic and role checks
   required when using privileged Supabase credentials. Authentication of every AI endpoint
   remains an active hardening item.
4. **PHI minimization:** text is analyzed before an LLM call. Names, Singapore NRIC/FIN
   identifiers, phone numbers, medical record numbers, and other configured entities are
   replaced with placeholders. Redaction maps remain server-side.

Patients cannot read internal comments or internal timeline content. Patient messages are
stored as patient-visible entries for the care team to review.

These controls are implemented for the current development architecture. Review the code,
environment, hosting, logging, and operational controls before any production deployment.

## Getting Started

### Prerequisites

- Node.js 20 or newer
- Python 3.11 or newer
- A Supabase project for a non-demo deployment
- Groq credentials for AI operations

### Install

```bash
npm install

cd ai-service
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m spacy download en_core_web_sm
cd ..
```

### Configure environment

Copy `.env.demo` to `.env` only for the supplied demo configuration. For local or hosted
development, create environment files from your own credentials and keep them untracked:

- Root `.env`: used by the AI service and collaboration server.
- `frontend/.env.local`: used by Next.js.

At minimum, configure the Supabase URL and keys required by the frontend, plus the service
role key and `GROQ_API_KEY` required by backend operations. The collaboration server also
needs the JWT verification configuration described in its source. Never commit credentials.

### Start services

```bash
# Frontend only
npm run dev:frontend

# AI service only
cd ai-service && .venv/bin/uvicorn main:app --reload --port 8000

# Collaboration server only
cd collab-server && npm run dev

# All services through the root script
npm run dev
```

Open http://localhost:3000. The AI service exposes interactive API documentation at
http://localhost:8000/docs, with `/health` and `/ready` available for service checks.

The root `dev:ai` script currently assumes `uvicorn` is available on `PATH`; using the
project interpreter command above avoids selecting a different system Python environment.

### Seed synthetic demo data

After configuring the root `.env` with a Supabase service-role key:

```bash
./scripts/seed.sh
```

The script creates or finds eight demo users across both clinics and calls the single
`seed_demo_data` database function. Apply `supabase/migrations/001_foundation.sql` to a
compatible Supabase project before seeding.

## Testing and Checks

Run the backend tests:

```bash
cd ai-service
.venv/bin/python -m pytest tests/ -v
```

Run the frontend type check and production build:

```bash
cd frontend
npx tsc --noEmit
npm run build
```

The test suite covers PHI redaction, clinical safety behavior, RBAC and cross-clinic scope,
revision history, provenance, concurrent edits, and self-learning importance behavior. The
integration suites require a reachable, seeded Supabase project and valid test credentials.

## Repository Layout

```text
frontend/                         Next.js application and UI
  app/                            Routes, layouts, and API handlers
  components/                     Editor, glance, timeline, patient, and shared UI
  lib/                            Supabase clients, hooks, stores, types, and Yjs provider
collab-server/                    Hocuspocus WebSocket server
ai-service/                       FastAPI application
  routers/                        HTTP endpoints
  services/                       Redaction, LLM, auth, persistence, and scoring
  tests/                          Unit and integration tests
supabase/migrations/              Current consolidated foundation migration
supabase/migrations_archive/      Historical migrations retained for reference
scripts/                          Demo seeding and measurement utilities
guardrails.md                     Binding security and engineering rules
CLAUDE.md                         Project architecture and phase plan
```

## Roadmap

The project plan is maintained in `CLAUDE.md`. The main areas of ongoing work are:

- Complete and verify the zero-trust foundation against live RBAC integration tests.
- Harden and authenticate every AI endpoint and validate the full PHI redaction path.
- Make collaborative persistence concurrency-safe, including version numbering and authorship.
- Improve the warm-path Glance View with measured, reproducible performance data.
- Close the five integration test suites against a seeded environment.

## License

MIT
