# Nightingale — Shared Longitudinal Patient Note System

A real-time collaborative patient note system that replaces fragmented EHR free-text notes with a single, shared, longitudinal care note. Built with progressive trust disclosure, AI-powered insights, and robust RBAC enforcement.

## Live Demo

**https://frontend-lake-five-85.vercel.app**

Login with any demo account below to explore the system.

## Local Quick Start

The fastest way to run Nightingale for demo/evaluation:

### Prerequisites
- Node.js 20+
- Python 3.12+

### 1. Clone and Install

```bash
git clone <repo-url> nightingale
cd nightingale

# Install all dependencies
npm install

# Install AI service dependencies
cd ai-service
pip install -e ".[dev]"
python -m spacy download en_core_web_sm
cd ..
```

### 2. Use Demo Environment

```bash
# Copy the pre-configured demo environment
cp .env.demo .env
```

The demo environment connects to a cloud Supabase project with pre-seeded data for two clinics.

### 3. Start the Application

```bash
# Start frontend and AI service (collab server optional for demo)
npm run dev:frontend &
npm run dev:ai
```

Or start all services (collab server will show "Local Only" without full secrets):
```bash
npm run dev
```

### 4. Login with Demo Accounts

Open http://localhost:3000 and login with any of these accounts:

**Nightingale Family Clinic:**
| Role | Email | Password |
|------|-------|----------|
| Clinician | `clinician@nightingale.demo` | `demo-clinician-2026` |
| Staff | `staff@nightingale.demo` | `demo-staff-2026` |
| Patient | `patient@nightingale.demo` | `demo-patient-2-2026` |
| Admin | `admin@nightingale.demo` | `demo-admin-2026` |

**Sunrise Medical Center** (separate clinic for RBAC demo):
| Role | Email | Password |
|------|-------|----------|
| Clinician | `dr.miller@sunrise.demo` | `demo-clinician-2026` |
| Staff | `emma.wilson@sunrise.demo` | `demo-staff-2026` |
| Patient | `robert.lee@sunrise.demo` | `demo-patient-2026` |

### Demo Mode Notes

- **"Local Only" Status**: Without collab server secrets, the editor shows "Local Only" mode with an amber indicator. This is expected behavior — all features work, edits save directly to Supabase.
- **Real-time Collaboration**: Requires `SUPABASE_JWT_SECRET` and `SUPABASE_SERVICE_ROLE_KEY` (not included in demo). The app gracefully degrades without these.
- **AI Features**: Summarization, highlights, and PHI redaction work with the included Groq API key.


## Architecture

```
Browser (Next.js 15)
  ├── HTTPS ──→ Supabase (Auth + PostgreSQL + RLS + Realtime)
  ├── WSS ────→ Hocuspocus Server (Yjs CRDT collaboration, port 1234)
  └── HTTPS ──→ FastAPI AI Service (PHI redaction + LLM, port 8000)
```

### Three-Process Architecture

| Service | Technology | Purpose |
|---------|-----------|---------|
| Frontend | Next.js 15 + TipTap + Yjs | SSR, collaborative editor, role-based views |
| Collab Server | Hocuspocus | WebSocket CRDT sync, persistence, JWT auth |
| AI Service | FastAPI + Presidio + Groq | PHI redaction, LLM summarization, importance scoring |

---

## Key Features

### Core Functionality
- **Shared Care Note Document**: Single longitudinal record per patient, replacing fragmented per-visit notes
- **Glance View ("Top Card")**: Critical flags, action items, and care plan score — readable in under 10 seconds
- **Longitudinal Timeline**: Time-ordered feed of all patient context with author attribution and provenance
- **Real-Time Collaboration**: CRDT-based editing with colored cursors and section-level presence
- **Graceful Fallback**: Works without collab server — "Local Only" mode with direct Supabase saves

### AI Integration
- **AI Scribe Summaries**: Distinct entries from doctor-patient, nurse-patient, and AI-patient sessions
- **Smart Highlights**: AI-generated insights with risk levels, provenance pointers, and confidence scores
- **Self-Learning Importance**: System learns from clinician accept/reject/pin actions to improve future suggestions
- **PHI Redaction Pipeline**: Names, NRIC, phones, MRNs redacted before LLM processing

### Trust & Provenance
- **Trust Badges**: Visual provenance on every piece of content (clinician-verified, AI-generated, patient-reported, staff-noted)
- **Progressive Trust Disclosure**: 3-layer confidence badges on all AI output
- **Provenance Pointers**: Click any highlight to jump to its source in the timeline

### Collaboration Features
- **Inline Comments**: Threaded comments with @mentions and resolve/unresolve states
- **Version History**: Full revision history with diff viewer and one-click revert
- **Save Confirmation Dialog**: Review changes before saving with selective save option
- **Patient Messaging**: Patients can send updates to their care team

### Data Management
- **Data Decay Policy**: 3-tier archival (hot/warm/cold) with automatic archiving after 6 months
- **Freshness Indicators**: Visual aging of timeline entries
- **Care Plan Completeness Score**: AI-assisted gap detection

---

## Demo Data

The demo includes two fully-populated clinics:

### Nightingale Family Clinic
- **Patient**: Alice Wong — Complex case with hypertension, declining kidney function (eGFR 62→45), pending cardiology referral
- **Timeline**: 8 entries spanning April 2025 to February 2026
- **Highlights**: 5 AI-generated insights including critical eGFR decline
- **Comments**: Staff-clinician collaboration on referral follow-up

### Sunrise Medical Center
- **Patient**: Robert Lee — Type 2 Diabetes with rising A1C (7.8%→8.2%)
- **Timeline**: 3 entries with lab results and AI consult summary
- **Demonstrates**: Multi-clinic isolation via RLS policies

---

## RBAC Enforcement

Access control is enforced at **two levels**:

### 1. Database Level (PostgreSQL RLS)
All data access is filtered by Row Level Security policies:
- **Clinic scoping**: Users can only access data within their clinic
- **Patient isolation**: Patients see only `patient_visible` entries
- **Role-based writes**: Staff cannot edit clinician entries and vice versa
- **Comment privacy**: Patients cannot see internal comments

### 2. UI Level
The frontend adapts views based on the authenticated user's role:
- **Clinician**: Full editor, AI highlights, accept/reject, comments
- **Staff**: Add staff notes, view timeline, staff-relevant highlights
- **Patient**: View patient-visible entries and send updates to care team
- **Admin**: Read-only oversight with audit log access

---

## PHI Redaction Pipeline

Located at: `ai-service/services/redaction.py`

```
Raw Text → Presidio Analyzer (spaCy NER + custom regex)
         → Detected entities (names, NRIC, phones, MRNs)
         → Presidio Anonymizer (replace with <PERSON_1>, <PHONE_1>)
         → Redacted text sent to Groq API (GPT-OSS-20B)
         → LLM response with placeholders
         → Server-side re-identification map (never sent to client)
```

Custom recognizers include:
- Singapore NRIC format: `[STFG]\d{7}[A-Z]`
- Local phone formats
- Medical record numbers

---

## Running Tests

```bash
cd ai-service
pytest tests/ -v
```

### Test Files

| File | What It Tests |
|------|--------------|
| `test_rbac_scope.py` | RLS policies, role isolation, cross-clinic denial |
| `test_revision_history.py` | Version tracking, revert, audit trail |
| `test_highlight_provenance.py` | Provenance pointers, risk reasons, referential integrity |
| `test_concurrent_edits.py` | CRDT merges, no data loss, concurrent operations |
| `test_self_learning_importance.py` | Interaction logging, score boosting, learning feedback |

---

## Performance & P95 Latency

### Target: ≤300ms Warm Path Glance View Load

The "At a Glance" view is optimized for sub-300ms P95 latency through:

1. **Server-Side Rendering (SSR)**: Next.js 15 pre-renders the glance view
2. **Denormalized `glance_cache`**: Pre-computed highlights and summaries stored on `care_notes` table
3. **React Query Caching**: Client-side cache with `staleTime` prevents redundant API calls

### Measuring P95 Latency

```bash
# Build production
npm run build && npm start

# Open Chrome DevTools → Network tab
# Navigate to /patients/[id] (glance view)
# Record "DOMContentLoaded" times across 20+ requests
```

- **Cold path**: ~800-1200ms (includes DNS, TLS, uncached queries)
- **Warm path**: ~150-300ms (target achieved)

---

## Project Structure

```
nightingale/
├── frontend/              # Next.js 15 (App Router)
│   ├── app/               # Pages and layouts
│   ├── components/        # React components
│   │   ├── editor/        # CareNoteEditor, SaveConfirmDialog, VersionHistory
│   │   ├── glance/        # TopCard, CriticalFlags, ActionItems
│   │   ├── timeline/      # TimelineView, TimelineEntry, EntryFilters
│   │   └── ui/            # Shared UI components, TrustBadge
│   └── lib/               # Utilities, types, Supabase client
├── collab-server/         # Hocuspocus WebSocket server
├── ai-service/            # FastAPI microservice
│   ├── routers/           # API endpoints (summarize, highlights, redact)
│   ├── services/          # Business logic (redaction, importance, llm)
│   └── tests/             # pytest test suite
├── supabase/              # Database migrations (001-014)
├── scripts/               # Setup utilities
├── .env.demo              # Pre-configured demo environment
└── .env.example           # Template for full setup
```

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd/Ctrl + S` | Save care note |
| `Cmd/Ctrl + B` | Bold text |
| `Cmd/Ctrl + I` | Italic text |

---

## License

MIT
