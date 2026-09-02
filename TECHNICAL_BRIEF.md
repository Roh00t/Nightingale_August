# Nightingale — Technical Brief

**A shared longitudinal patient note where the AI is never trusted on its own.**

The hard part of this problem is not summarisation. It is that a risk badge, a
confidence label and an importance score are all just numbers on a screen, and a
build can render all three without any of them meaning anything. This brief is
organised around the three questions that matter for each: what is it, how would
we know if it were wrong, and what happens when it is.

**442 automated tests, runnable offline with no credentials.**

**Compliance posture.** Synthetic data only. The design is HIPAA/PDPA-*informed*
— PHI redacted before egress, access enforced at the database, audit records
carrying counts rather than content — and is **not a compliance attestation**.
Real patient data would additionally require BAAs or equivalent with Groq,
ElevenLabs, Supabase and Railway; defined audit-log retention and review;
encryption-at-rest key custody; access reviews; and breach notification. None of
that exists here.

**Live deployment.** Three tiers, three hosts:

| Tier | Host | URL |
|---|---|---|
| Frontend — Next.js 15 | Vercel | https://nightingale-august-frontend-6ktv.vercel.app |
| Database — PostgreSQL, 11 RLS tables | Supabase | managed |
| AI service — FastAPI | Railway | https://nightingaleaugust-3zme-production.up.railway.app |

The Hocuspocus collab server is the one tier that is **not** deployed. It holds a
stateful WebSocket per session with the authoritative Y.Doc in memory; serverless
functions are short-lived and share no memory between invocations, so a CRDT
authority cannot live there. The client detects its absence and degrades to
single-user local mode — an amber "Local Only" badge, `yjs_state` loaded from and
written straight back to Supabase. Edits persist and survive reload; what is lost
is live cursors and simultaneous co-editing. §5 real-time sync is therefore
exercised locally and by the suite; the §8 safety layer runs on Railway.

---

## Twelve Core Architectural Criteria

The architecture is built around three distinct trust signals (clinical severity, system reliability, and workflow urgency) rather than a single meaningless confidence score. This brief evaluates the system against the twelve core clinical integration criteria, explicitly highlighting where we enforce strict design boundaries over hallucinated capabilities.

## 1. Audio Processing & Multilingual Extraction

**Within-Statement Code-Switching:** We utilize ElevenLabs Scribe v2 without language code pinning. This prevents forced English phonetics, capturing regional dialects and code-switching natively.

**Multilingual Downstream Processing:** Downstream AI processing handles unpinned phonetic outputs organically. We extract directly from the raw transcript rather than forcing a lossy translation layer.

**Speaker Attribution and Diarization:** Diarization is handled upstream by Scribe v2. Crucially, speaker labels explicitly survive our PHI redaction layer, ensuring that clinical provenance ("I've been dizzy" from a patient vs. clinician) is maintained before any LLM sees the text.

## 2. Provenance & State Preservation

**Immutable, Version-Bound Provenance:** The note_versions table retains full historical snapshots in JSONB. Reverting is additive—restoring a previous version writes a new sequential snapshot, ensuring the superseded state is permanently recoverable.

**Real-Time Collaborative Editing Without Lost Updates:** Real-time editing is mediated via a Hocuspocus server and Yjs CRDTs over WebSockets. If the collab server is unreachable, the system elegantly degrades to a single-user local mode, saving yjs_state directly to Supabase via strict Optimistic Concurrency Control.

**AI Regeneration that Preserves Human-Confirmed State:** AI edits cannot silently overwrite human work. File writes happen server-side; a browser session cannot forge an AI-authored system entry, and deterministic advisory locks prevent concurrent flush collisions during version allocation.

## 3. Fact Extraction & Clinical Contradiction

**Fact Extraction Under Negation:** The system utilizes strict extraction over generation. If a claim is not a byte-exact substring of the source, it is rejected and never stored. Deterministic regex risk floors prevent negated findings (e.g., "anaphylaxis ruled out") from escalating to critical alerts.

**Explicit Handling of Contradictory Assertions:** Human-human and cross-role contradictions are surfaced server-side via /api/ai/conflicts. The system never arbitrates; if a clinician writes 10mg and a nurse writes 100mg, it displays both verbatim for human resolution.

**Self-Learning (Clinic-Scoped & Bounded):** The learning loop is strictly clinic-scoped and resistant to alert fatigue. Critical items have a 0.90 importance floor that learned weight cannot bury, and dismissal bursts (>10 in 5 minutes) are excluded from training. We mitigate exposure bias by randomly promoting 5% of unsurfaced items into the review queue.

## 4. Strict Design Boundaries & Deliberate Omissions

Based on our recent vulnerability assessments, we deliberately omit certain automated features that pose unacceptable clinical risks.

| Feature Category | Architectural Stance & Omission Rationale |
|---|---|
| Streaming Real-Consult Audio | We cap audio at 120s and process whole-file transcripts after capture. Streaming metered audio without server-side boundaries risks silent credit exhaustion. Validating file size before the metered step is our absolute control. |
| Medical Terminology & Dosage Confirmation | We refuse to perform LLM-based dosage validation. Grounding confirms transcription accuracy (set membership, not substring matching), but determining clinical safety requires an external, deterministic, licensed national formulary. |
| Distinct Audience Readability | Patient-facing text is heavily gated against prohibited speech acts (diagnosis, prognosis) and requires named human approval. We prioritize strict redaction over generative "dumbing down" of clinical text, which risks altering medical meaning. |

---

## 5. Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Browser — Next.js 15 App Router                    (PWA · installable)  │
│                                                                          │
│   /patients/[id]  SERVER COMPONENT                                       │
│     └─ one indexed read of care_notes.glance_cache  ──► Top Card in HTML │
│     └─ <SunshineBlock>   open actions · AI share · confidence · audit     │
│     └─ <PatientWorkspace> (client)                                       │
│          timeline · comments · highlights load separately, so history    │
│          volume never blocks the card                                    │
│     └─ <VoiceCapture>    MediaRecorder, 120s hard cap, single-flight     │
└───────┬────────────────────┬─────────────────────────┬───────────────────┘
        │ HTTPS              │ WSS                     │ HTTPS + JWT
        ▼                    ▼                         ▼
┌───────────────┐   ┌──────────────────┐   ┌──────────────────────────────┐
│   SUPABASE    │   │ HOCUSPOCUS local │   │  FASTAPI — Railway (prod)    │
│               │   │      :1234       │   │                              │
│ Auth (GoTrue) │   │ JWT verify (JWK  │   │  require_caller()  JWT gate  │
│ Postgres      │   │  set, by kid)    │   │        │                     │
│ ROW LEVEL     │   │ ROLE allowlist   │   │        ▼                     │
│  SECURITY ◄───┼───┤  patient=REJECT  │   │  transcription.py  (mock 1st)│
│ 11 tables     │   │  admin=readonly  │   │   └─ ElevenLabs Scribe v2    │
│ Realtime      │   │ clinic match     │   │      2 switches required     │
└───────────────┘   │ Yjs CRDT sync    │   │        │                     │
        ▲           │ create_note_     │   │        ▼                     │
        │           │  version() —     │   │  redaction.py                │
        │           │  advisory lock   │   │   Presidio + spaCy + SG regex│
        │           └────────┬─────────┘   │        │                     │
        │                    │             │        ▼                     │
        │  service-role key  │             │  ╔══════════════════════════╗│
        └────────────────────┴─────────────┤  ║   CLINICAL SAFETY LAYER  ║│
           RLS bypassed — tenant and role  │  ║  extraction   (verbatim) ║│
           checks re-applied in code (S3)  │  ║  risk_rules   (floors)   ║│
                                           │  ║  confidence   (abstain)  ║│
                                           │  ║  conflict     (surface)  ║│
                                           │  ║  patient_gate (2 gates)  ║│
                                           │  ║  feedback     (floors)   ║│
                                           │  ╚═══════════╤══════════════╝│
                                           │              ▼               │
                                           │        GROQ  gpt-oss-20b     │
                                           │   sees redacted text only    │
                                           └──────────────────────────────┘
```

**Browser-to-service CORS.** The AI service is called directly from the browser,
so it owns the CORS decision and no Vercel setting affects it. Production and
localhost origins are listed explicitly; preview deployments are matched by
`VERCEL_PREVIEW_ORIGIN_REGEX`, which is deliberately narrower than the obvious
pattern because `*.vercel.app` is a shared namespace — a loose `...6ktv.*` would
grant CORS to anyone registering a similarly-named project, and `.*` spans dots
so it would admit nested labels too. Both were measured against the loose form.
Starlette matches with `fullmatch`, which is what defeats a `.vercel.app.evil.com`
suffix. `allow_origins` is never `*`: with `allow_credentials=True` browsers
reject it outright.

A rejected preflight returns `400` with **no** `Access-Control-Allow-Origin`
header, so it surfaces in DevTools as a generic CORS failure rather than as a
list this service controls — worth knowing, because the instinct is to look for a
gateway problem.

**Endpoints.** All `/api/ai/*` require a verified JWT, are **POST-only** (other
methods return `405`), and fail closed.

| Path | Purpose |
|---|---|
| `/api/ai/summarize` | structured clinical summary |
| `/api/ai/highlights` | risk-scored highlights, through the full safety layer |
| `/api/ai/redact` | PHI redaction; counts only, map never leaves the server |
| `/api/ai/scribe` | AI-scribed consult → system-authored timeline entry |
| `/api/ai/conflicts` | cross-author clinical contradiction detection |
| `/api/ai/send-patient-message` | maker-checker gate, then the only write to a patient-visible entry |
| `/api/ai/transcribe` | ambient voice → diarized → redacted → structured |

Four processes, one Postgres. The safety layer sits between the model and the
record in **both** directions: text is redacted on the way out, and every claim
is validated on the way back before anything is stored.

---

### Deployment mechanics

| Tier | Builder | Notes |
|---|---|---|
| Vercel | Next.js | `regions: ["sin1"]`; `NEXT_PUBLIC_*` inlined at **build** time, so a dashboard edit needs a redeploy |
| Railway | **Nixpacks** (not Docker) | `railway.json` + `Procfile`; `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| Supabase | migrations | `DEPLOY_20260901_all.sql`, four migrations in dependency order |

**The spaCy model is not a pip dependency.** `en_core_web_sm` ships as a wheel
outside PyPI, so `pip install -r requirements.txt` does not bring it and the
redaction engine will not start without it. It has to be installed during the
**build** rather than on first request: a runtime download puts a multi-second
cold start on the PHI path, and a network failure there fails the request that
most needs to succeed. Pinning the wheel URL in `requirements.txt` is preferable
to a separate build command, because it makes the model a declared dependency
rather than an out-of-band step someone can forget. `/ready` reports
`redaction_engine` either way.

**CORS changes require a Railway redeploy.** The header is emitted by FastAPI, so
a frontend deploy has no effect on it — a distinction that costs an hour the
first time it bites.


## 6. Data model

```
clinics ─┬─< profiles ──────────────< interaction_log
         │      │  (role enum: patient/staff/clinician/admin)
         │      │
         └─< care_notes  ── glance_cache jsonb ── the ≤300ms read
                 │
                 ├─< timeline_entries ──< comments (threaded, self-ref parent)
                 │        │                   author_id → profiles
                 │        │  author_role · author_id · type · timestamp
                 │        │  provenance_pointer · visibility · is_archived
                 │        │
                 │        └─< highlights  source_entry_id → timeline_entries
                 │              provenance_pointer {source_type, source_id, span}
                 │              risk_level · risk_floor · model_risk
                 │              confidence_score · confidence_band · abstained
                 │              importance_score · safety_metadata
                 │
                 └─< note_versions  UNIQUE(care_note_id, version_number)
                        content_snapshot jsonb — the ACTUAL note text
```

**Three quantities, never collapsed into one.** The schema keeps them in
separate columns because rendering one as another is what makes a trust signal
meaningless:

| Column | Question it answers |
|---|---|
| `risk_level` | how bad is this if true — clinical severity |
| `confidence_score` | how much should I trust the claim — system reliability |
| `importance_score` | where does it sit in my queue — workflow urgency |

The frontend previously passed `importance_score` into the trust badge's
`confidence` field. That is fixed; they are now three separate signals.

**Provenance is a discriminated union on `source_type`**, because two different
link types are needed and writing them ad hoc is how they drift apart:

```jsonc
// AI-scribed entry → the recording it came from
{"source_type": "scribe_session", "session_id": "sess-…", "ai_model": "…",
 "recording_duration_sec": 1245}

// highlight → the entry and exact characters it came from
{"source_type": "timeline_entry", "source_id": "<uuid>",
 "span": {"from": 20, "to": 56}}
```

**One migration, not fourteen.** The inherited chain was a cautionary tale: 014
dropped the policies 006/007 created and reinstated the exact nested-`EXISTS`
pattern they existed to remove; 013 re-hardcoded a value 008 had just repaired
and redefined the seed function at a new arity, making the two-clinic version
permanently unreachable. `001_foundation.sql` states the intended final state
once. One definition per policy.

---

## 7. Security

### RBAC at the database

RLS on all eleven tables; the UI adapts to role but is never the control. Clinic
scoping runs through `SECURITY DEFINER` helpers with pinned `search_path`,
because a nested `EXISTS` on an RLS-protected table re-evaluates that table's RLS
and raises `42501`.

The load-bearing distinction: `check_care_note_access()` answers clinic
membership only and returns true for *every* note in the caller's clinic. Used
in a patient policy it exposes other patients' records — a leak the historical
chain carried. **Patient policies scope on ownership via
`check_patient_owns_care_note()`, never on clinic.**

Patients cannot read AI-scribed notes, enforced twice: entries are
`visibility='internal'`, *and* the patient SELECT policy excludes those entry
types by name. A mis-marked entry stays hidden.

Staff and clinicians cannot overwrite each other **on timeline entries**: the
only UPDATE policy there is `author_id = auth.uid()`. A cross-role write changes
zero rows.

**Edit rights on the care note itself are weaker than the UI implies**, and the
distinction is worth stating precisely rather than claiming a guarantee that does
not hold. The intent is that the note body belongs to the clinician and staff work
through timeline entries and comments. The editor enforces exactly that — staff
and admins get a read-only surface with a stated reason. The database does not:

```sql
CREATE POLICY "Care team can update care notes"
  ON care_notes FOR UPDATE
  USING (clinic_id = get_user_clinic_id()
         AND get_user_role() IN ('clinician', 'staff', 'admin'));
```

That is the row-versus-column problem again, in a second place. The note body
(`yjs_state`) and the care plan (`glance_cache`) are columns of the *same row*,
and staff legitimately own care-plan work — ticking off plan items is theirs. A
row-level policy cannot grant one column and withhold the other, so admitting
staff to the care plan admits them to the note body as well.

| Write path | Gate | Staff reach the note body? |
|---|---|---|
| Browser → PostgREST | RLS `Care team can update care notes` | yes |
| Browser → Hocuspocus → service-role | `COLLAB_WRITE_ROLES` | yes — `["clinician", "staff"]` |
| Editor UI | read-only surface for staff/admin | no |

So clinician-only authorship of the note body is currently a **UI convention**.
Closing it means either splitting the body into its own table — the fix already
applied to the clinical assessment below — or narrowing `COLLAB_WRITE_ROLES` to
`["clinician"]` and adding a column-scoped `BEFORE UPDATE` trigger that rejects a
staff write touching `yjs_state`. The first is cleaner and matches the pattern the
codebase already uses; both are more than a documentation change, so this is
recorded as open rather than quietly overstated.

**Where RLS is bypassed** (service-role key), tenant and role checks are
re-implemented in code — AI scribe ingestion, the collab server, and account
provisioning. Each is listed in the README with its replacement check.

### Transport and storage

TLS in transit and encryption at rest are both provided by the platform rather
than implemented here, and it is worth being explicit about which is which:

| Layer | Control | Provided by |
|---|---|---|
| Browser ↔ Supabase | TLS 1.2+, HTTPS enforced, HSTS | Supabase (managed) |
| Browser ↔ AI service | TLS terminated at the platform edge in deployment; plain HTTP on localhost only | Railway / reverse proxy |
| Browser ↔ collab server | WSS in deployment; `ws://` on localhost only | platform |
| Data at rest | AES-256 on the Postgres volume and in backups | Supabase (managed) |
| Secrets | `.env` is gitignored; the service-role key and JWT signing key are never sent to the browser | this repo |

Two things this repo does own. The `SUPABASE_SERVICE_ROLE_KEY` appears only in
server-side code — never behind a `NEXT_PUBLIC_` prefix, so it cannot reach a
client bundle. And redaction maps, which hold the reverse mapping from
placeholder back to real PHI, are held in a bounded in-process store with a TTL
and are never serialisable into a response; the `/api/ai/redact` response model
exposes counts only, asserted by test.

### Patient data isolation — why the component boundary was not enough

RLS correctly lets a patient read their own `care_notes` row. That does not make
every column in it patient-facing. `glance_cache.top_items` held the **internal
clinical assessment** — severity bands (`CRITICAL`, `HIGH`), model confidence,
and open clinical actions such as "eGFR declining 62 → 45".

The first fix filtered those fields in the `/patients/[id]` server component, so
they never entered the RSC stream. That was described here as enforcement rather
than UI hiding. It was not. **RLS is row-level, not column-level.** A patient
holding a normal session could skip the page and ask PostgREST directly:

```
GET /rest/v1/care_notes?select=glance_cache
Authorization: Bearer <the patient's own access token>

{"text": "eGFR declining: 62 → 45 over 6 months",
 "risk_level": "critical", "confidence": 0.92}
{"text": "Cardiology referral pending since Jan 15", "status": "unresolved"}
```

The component boundary hid the data from the page while leaving it readable by
the patient. Recording this is the point: the portal looked correct throughout,
and every UI-level test passed.

The separation is now structural. The assessment lives in
`care_note_assessments`, keyed by `care_note_id`, with three care-team policies
and **no patient policy at all** — not a filter a patient fails, but the absence
of any rule that could admit them. The consult page fetches it only for
clinician/staff/admin and recomposes it into `glance_cache` in memory, so
downstream components keep the shape they already expect.

| Field | Patient | Care team |
|---|---|---|
| `glance_cache.care_plan_score`, `care_plan_items`, `last_visit` | ✅ | ✅ |
| `care_note_assessments.top_items` (severity, confidence, open actions) | ❌ no policy admits them | ✅ |
| `care_note_assessments.changes_since_last_visit` | ❌ no policy admits them | ✅ |
| Raw AI-scribed entries | ❌ excluded by RLS, twice | ✅ |
| Internal comments, highlights, versions | ❌ no policy admits them | ✅ |
| Contradiction badges | ❌ never rendered | ✅ |

Verification runs against the **API**, not the rendered page, because a UI test
would have passed for as long as the data was exposed. Six assertions in
`test_rbac_scope.py` cover the patient path and the care-team control, so a later
change cannot satisfy them by breaking clinician access;
`scripts/verify_patient_isolation.mjs` runs the same checks against a live
deployment.

Both look for the *shape* of a clinical judgement — a severity band, a
confidence, a triage status — rather than for clinical words. A patient's own
care plan legitimately reads "Consider nephrology consult if eGFR continues to
decline"; that is an instruction written for them, and a keyword scan flags it
wrongly.

`SunshineBlock` still refuses to render internal flags for a patient role. It is
now defence in depth behind a real control, rather than the control itself.

### PHI redaction — an accuracy control, not only a privacy one

Presidio + spaCy `en_core_web_sm`, plus custom Singapore recognisers, running in
`ai-service/services/redaction.py` **strictly before** any Groq call. NRIC/FIN
including the 2022 **M series**; SG phones across mobile/landline/`+65` with
internal spacing; titled and role-labelled names for Chinese, Malay and Tamil
forms that `en_core_web_sm` misses.

Over-redaction is treated as a defect of equal weight, because a mangled note is
a clinical hazard:

- a clinical allow-list stops `Lisinopril` and `Metformin` being redacted as
  PERSON — spaCy tags them as people;
- `DATE_TIME` is deliberately not redacted: clinical reasoning depends on
  relative timing, and dates alone are low re-identification risk once names,
  NRIC, phone and MRN are gone;
- negation is never stripped — `"not allergic to penicillin"` losing its `not`
  is asserted against by test.

Logs record counts and entity types, never PHI. 33 tests, each written as "this
string must NOT appear in the text we would send to Groq".

---

## 8. Clinical safety layer

### 8.1 Extraction over generation

**Decision made before any prompt was written: extraction.** A paraphrase
retains no origin, so there is nothing to validate. Every claim must be an exact
substring of a source entry.

- *What it is:* a byte-exact span in a named source entry.
- *How we'd know it's wrong:* the quote either occurs in the source or it does
  not. No model, no threshold, no judgement.
- *What happens when it is:* the claim is **rejected and never stored**. A
  hallucination degrades recall, never correctness.

Normalisation folds whitespace and typographic quote characters only. Anything
that changes words — including dropping a negation — fails. A rejected claim gets
span `(0,0)`, never a guessed offset: a fabricated span points a clinician at
text that does not support the claim, which is worse than no span. Rejection rate
is exposed as a drift signal; a rising rate means the model has slid toward
paraphrase.

### 8.2 Deterministic risk floors

```
final_risk = max(deterministic_floor, model_proposal)
```

An LLM's ordinal is not stable across runs, prompt phrasings or model versions.
It is treated as a *proposal*. The floor is computed by regex and numeric
comparison — reproducible, inspectable, diffable in review, unchanged by a model
upgrade.

**The model can raise risk. It can never lower it.** Text containing
`anaphylaxis` resolves to critical whether the model said `info` or `critical`.

Numeric thresholds (K⁺ ≥ 6.0, eGFR ≤ 30, systolic ≥ 180, SpO₂ ≤ 92) because a
number does not drift the way an adjective does. Negation is guarded in **both
directions** — `"denies chest pain"` and `"anaphylaxis ruled out"` both fail to
escalate. Over-firing on negated findings is a direct alert-fatigue driver.

Every floor names the rule that produced it, so a wrong badge traces to a
specific pattern rather than to model temperament.

### 8.3 Confidence and abstention

Self-reported model confidence is decoration, so the model is never asked. The
score is computed from three observable signals:

```
confidence = 0.50 × ensemble agreement      (same claim across N samples)
           + 0.35 × extraction verification (verbatim in source?)
           + 0.15 × deterministic rule support
```

| Band | Range | Meaning shown to the clinician |
|---|---|---|
| High | ≥ 0.85 | consistent across samples and verbatim in the record |
| Medium | 0.60–0.84 | mostly consistent; verify before acting |
| Low | < 0.60 | **abstain** — withheld and sent to manual review |

"Medium" therefore has an exact numeric meaning, published in the UI on hover
rather than left as a word the model chose.

- *How we'd know it's wrong:* calibration. `brier_score()` and a per-band
  accuracy report over resolved items. If the system says 0.9 it should be right
  about 90% of the time; a rising Brier score means the weights need refitting.
- *What happens when it is:* below 0.60 the system **does not guess** — it emits
  an abstention surfaced as a review task rather than a claim.

**One deliberate asymmetry.** A low-confidence *critical* finding is still shown,
flagged unverified. Silently withholding a possible anaphylaxis is a worse
failure than showing a clinician something uncertain. Abstention protects
against noise, not against safety-relevant recall.

### 8.4 Clinical contradiction engine

Human-human contradictions are real and routine. This is a different problem
from resolving competing *edits*, and is handled differently.

Detection runs server-side only, at `POST /api/ai/conflicts`. It briefly ran in
two places — this module and a hand-maintained TypeScript port that let the UI
flag contradictions without a round trip — but nothing enforced that the two
agreed, so they could drift until one flagged a dosing conflict and the other
did not. For a safety control that is not an acceptable failure mode, so the
port was deleted.

Deterministic regex extracts medication–dosage pairs, allergy assertions and
their explicit denials, tagged with author, role, timestamp and verbatim quote.
Where one entity carries two values across two authors, a conflict is raised:
allergy contradictions rank **critical**, dosage **high**.

**The system never arbitrates.** If a clinician wrote `10mg` and a nurse recorded
`100mg`, it has no basis to decide which is correct, and picking one would
manufacture false certainty about a dosing decision. It surfaces the delta with
both quotes side by side and states that a clinician must resolve it.

**Three filters remove distinct classes of false positive**, because a badge
that fires on non-events trains a care team to ignore it:

*Same-author revisions.* One clinician amending their own note is a correction.

*Repeated identical values.* Deduplication keys on the **value**, not on
`(author, value)`. Eight people each recording `10mg` is eight authors
*agreeing*; rendering that as "10mg vs 10mg vs 10mg…" makes unanimity look like
eight-way disagreement and buries the single value that actually differs. One
representative claim survives per distinct value — the earliest, since that is
who first committed to it — and `agreed_by` records how many others concurred so
attribution is not lost.

*Dose titration.* Alice Wong's Lisinopril went `5mg → 10mg` because her clinician
increased it. That is a **change**, not a disagreement.

The titration rule deliberately does **not** ask "is the lower dose older?".
That would hide a genuine de-escalation error while still flagging ordinary
tapering — backwards, since dose *direction* carries no information about whether
two people disagree. It asks instead: **did anyone assert a value the prescriber
never did?**

| Scenario | Verdict |
|---|---|
| Clinician `5mg` → `10mg`, echoed by staff and the scribe | titration — suppressed |
| Clinician `10mg` → `5mg` (tapering), echoed by staff | titration — suppressed |
| Clinician `10mg`, nurse records `100mg` | **conflict** — a value never prescribed |
| `10mg` → `5mg` → `10mg` interleaved | **conflict** — the record contradicts itself |

Against the live seeded timeline this took the engine from one conflict carrying
eight assertions (`5mg` plus seven duplicate `10mg`) to zero, while every genuine
contradiction above still fires.

*Vitals* are excluded entirely: a blood pressure differing between April and
October is the timeline working as intended.

Edit-level conflicts are separate and *do* resolve, deterministically: role
authority → recency → edit id. The id tie-break exists so two clients resolving
the same conflict independently cannot diverge. Losing edits are preserved, never
discarded. Clinician-over-AI resolves silently; two humans disagreeing still
picks a winner but is flagged for review.

### 8.5 Patient-facing maker-checker firewall

Patient-facing generation is the highest-severity class in the system, and the
only path where AI output cannot reach its audience unaccompanied. Three gates,
all of which must pass:

1. **Grounding (deterministic).** Every clinical token — drug names, doses,
   numbers with units — must appear in the source entries. Compared by **set
   membership, not substring**: substring matching would treat `1` as grounded by
   `10mg`, and `10` as grounded by `100mg`, which is a dosing error passing in
   the dangerous direction.
2. **Prohibited speech acts (deterministic).** Diagnosis, prognosis,
   stop-treatment, dose-change and emergency-deferral language. These are
   clinician speech acts; an assistant has no standing to make them.
3. **Named human approval.** Clinician or admin only, recorded and rendered as
   visible attribution so the patient knows a human reviewed it and who.

**Approval cannot rescue a blocked draft.** A clinician clicking approve on
ungrounded content is precisely what gates 1 and 2 exist to prevent, so the
checks run first. A blocked draft is returned for editing — never softened or
auto-corrected, because silent repair hides the failure.

**Where the gate sits, and why that is most of the design.** For a while this
module existed with passing unit tests and *nothing called it*. The clinician
pressed Send and the browser inserted straight into `timeline_entries`, so the
check that stops `10mg` becoming `100mg` was not in the path a message travels.
Three properties fix that, and each closes a different hole:

| Property | Hole it closes |
|---|---|
| The **edited** text is screened, at the moment of Send | The AI's draft may be grounded; the clinician's edit of it is the thing the patient reads |
| Sources are read **server-side** from the record | If the caller supplied them, a fabricated dose could be sent as its own grounding and verify against itself |
| The write happens on the **passing branch of the same call** | A check the browser runs before its own insert is advice, skipped by any request made outside the UI |

The last one needs the database, not just the endpoint. `POST
/api/ai/send-patient-message` files the approved entry with the service-role key,
and both care-team INSERT policies on `timeline_entries` now carry
`AND visibility = 'internal'`. A clinician's own token — in curl, in Postman —
can write internal notes all day and cannot create a patient-visible row at all.
The only route to the patient runs through the gate.

Blocked responses return `422` with the offending tokens, and the UI renders them
as highlighted chips beside the draft rather than a generic failure, so the
clinician is pointed at `100000000mg` instead of re-reading their own message
looking for what upset it.

### 8.6 Feedback loop: exposure bias and fatigue

Both hazards are structural, and neither is fixed by better prompting.

**Exposure bias.** The loop only sees what it surfaced. Score something low, hide
it, and nobody corrects it — the error is self-reinforcing and invisible, because
precision on surfaced items looks fine while recall quietly rots. Mitigation:
**5% of unsurfaced items are randomly promoted into a review queue.** Random, not
"most nearly surfaced" — sampling near the threshold measures the boundary rather
than the blind spot.

**Alert fatigue.** A team under load clicks dismiss, and learning from those
dismissals teaches the system to hide exactly what it should show.

- Critical items have an importance floor (0.90) that learned weight cannot bury.
- Critical dismissals require a typed reason and cannot be bulk-dismissed.
- Low-risk noise stays one click, because friction on noise is itself a fatigue
  driver.
- Dismissal bursts (>10 in 5 minutes) are honoured in the UI but **excluded from
  training**, so a bad shift does not permanently degrade the model.
- Interactions with critical items never train the model at all — those classes
  are governed by deterministic floors, and letting them drift would defeat the
  floor.

The learned signal is **clinic-scoped**. One clinic's pins provably do not move
another clinic's scores; there is a test for exactly that.

---

### 8.7 Ambient voice capture

The newest surface, and the one with a cost model attached. ElevenLabs Scribe v2
is metered against a 10,000-credit budget, so the design constraint was not
accuracy but *making it impossible to spend credits by accident*.

**Two independent switches.** A live call requires `?live=true` on the request
**and** `ELEVENLABS_LIVE_ENABLED=true` on the deployment. One is not enough: a
stray query parameter in a fixture, a copied curl command, or a browser retry
would each individually be sufficient, and the failure is silent until the
balance is gone. No test sets the environment flag, so the entire suite is
structurally incapable of reaching the meter — asserted by a tripwire test.

**The SDK is an optional dependency**, imported lazily inside the live branch
only, so the suite runs with the package absent entirely.

**Ordering carries the safety properties:**

```
MediaRecorder (120s hard cap, single-flight)
  → size + MIME gate      413 BEFORE anything metered runs
  → Scribe v2 (or mock)   diarized, speaker-labelled
  → redaction.py          PHI stripped BEFORE any LLM sees the text
  → structuring LLM       redacted dialogue only
  → de-redact + verify    no placeholder survives into the record
  → ai_*_summary entry    author_role='system', author_id=NULL
```

The size check runs before transcription because transcription is the metered
step — validating afterwards would spend credits to discover the upload was
never acceptable. The 120-second client cap and the 5 MB server cap are
independent: the client cap is a courtesy, the server cap is the control.

**Speaker labels survive redaction.** "I've been dizzy" means something
different from the clinician than from the patient, and provenance back to a
segment depends on the label surviving. The redactor removes names, not the
structure of the dialogue.

**One asymmetry, made deliberate.** The returned transcript is redacted; the
returned summary is de-redacted. The summary becomes the clinical record, and a
clinician reading `<PERSON_1>` in a note has been handed a broken record. The
transcript is a working artefact, and there is no reason to ship identifiers
twice in one response. Both directions are asserted by test so the asymmetry
cannot drift into an accident.

**Filing happens server-side, and it has to.** Every INSERT policy on
`timeline_entries` requires `author_id = auth.uid()`, while an AI-scribed entry
carries `author_role='system'` with `author_id = NULL`. `NULL` cannot equal a
uuid, so the write is impossible from a browser session for **every** role —
clinician and admin fail exactly as staff does. That is the policy working: a
session able to write `author_role='system'` could forge a note attributed to the
AI scribe.

So `/api/ai/transcribe` accepts an optional `care_note_id` and performs the write
itself with the service-role key, which is the only credential that can produce a
system-authored row. Because that key bypasses RLS, the checks RLS would have
applied are re-implemented in the handler (§3, S3):

| Check | Enforced by |
|---|---|
| Care note belongs to the caller's clinic | `resolve_care_note()` — a foreign note returns **404**, not 403, so it cannot be probed for existence |
| A patient may only file to their **own** care note | explicit `patient_id` comparison — clinic match alone would let them write into a peer's record |
| The capture mode matches the caller's role | patients are restricted to `patient_session` |
| Accountable human origin | `captured_by` / `captured_by_role` in metadata, since `author_id` is NULL |

Whisper-style per-segment confidence feeds the ensemble term in §4.3, which is
otherwise the weakest input; segment timestamps make provenance audio-anchored
rather than text-anchored.

### 8.8 Revision history and revert

`note_versions.content_snapshot` is `jsonb` holding the **actual note text** at
that point in time, plus a `sections` breakdown:

```jsonc
{ "text": "ASSESSMENT: eGFR dropped to 45 (from 58)…\nPLAN: Increased Lisinopril to 10mg…",
  "sections": { "assessment": "…", "plan": "…" } }
```

It previously held a *description* of the change — `{"summary": "Added follow-up
notes and medication"}` — which cannot be reverted **to**: restoring it would
replace the note body with that sentence. The description belongs in
`change_summary`, and now lives there alone.

**Revert is additive.** Restoring v1 writes a *new* version carrying v1's
content, so the superseded state stays recoverable and the revert itself is
auditable. Numbering is allocated by `create_note_version()` under a
per-care-note advisory lock, so a concurrent flush cannot collide on
`UNIQUE(care_note_id, version_number)`.

**The verification was tautological and is not any more.** The old test inserted
`content_snapshot = old["content_snapshot"]` and asserted the inserted row
equalled it — proving the database stored what it was handed, and nothing about
revert. It passed for months while snapshots held unrestorable descriptions. The
test now compares three distinct states, asserts the target and current differ
*before* reverting, rejects snapshots that read like changelog entries, and
confirms the superseded state survives.

## 9. Performance — the ≤300ms warm glance path

**Decoupling, then measurement.** The page was a client component running a
waterfall — session → care_note → (entries, comments, highlights) → profiles —
behind a skeleton, so the Top Card could not paint until history had loaded. The
budget was structurally unreachable.

Now `/patients/[id]` is a server component performing **one indexed read** of
`care_notes.glance_cache` (`idx_care_notes_patient`), selecting explicit columns
so the `yjs_state` bytea stays off the path entirely. The card is in the server
HTML. Timeline, comments and highlights load client-side, so historical volume
has no bearing on how fast the card appears.

**Method.** `scripts/measure_glance.mjs` authenticates against Supabase to
exercise the real authenticated render path, discards 10 warmup requests (a warm
path means caches and connections are primed; including cold starts measures the
wrong thing), then times N sequential requests to last byte. Sequential by
design — concurrency measures throughput, which is a different claim than
single-request latency.

| | Latency |
|---|---|
| min | 54.3 ms |
| mean | 69.9 ms |
| **P50** | **68.4 ms** |
| **P95** | **79.7 ms** |
| **P99** | **96.5 ms** |
| max | 239.7 ms |

**P95 79.7 ms against a 300 ms budget — 3.8× headroom. n = 100 sequential warm
requests, 15 warmup requests discarded.**

Reproduce with `node scripts/measure_glance.mjs --n 100 --warmup 15` against a
production build (`npm run build && npm start`).

**What is included.** Full server render of the authenticated route, including
the Supabase round trip for the `glance_cache` read, measured to last byte of
HTML. The Supabase project is remote, so network time to the database is inside
these numbers rather than excluded — the figure is end-to-end for the page, not
just local compute.

**What is excluded**, and stated so the number is not read as more than it is:
browser parse, hydration and paint; the client-side timeline, comments and
highlights fetches, which are deliberately off this path; and cold starts.

**The harness aborts rather than reporting a fast wrong number.** An early run
reported P95 3.1 ms — it was timing a redirect to `/login`, because a
hand-written session cookie did not match the format `@supabase/ssr` 0.5.2
expects. The script now builds the cookie with the library itself and exits
non-zero if any sampled response is not 200. A latency measurement that silently
measures the wrong endpoint is worse than no measurement, because it looks like
success.

Server HTML was separately verified to contain the rendered glance content
(`eGFR`, `Cardiology referral`, the care plan score) rather than an empty shell,
confirming the card is server-rendered and not hydrated in afterwards.

**Data decay.** Three tiers: hot (<6 months, in every query), warm (archived,
excluded by default, reachable by clinicians and admins), cold (off-database,
designed not implemented). `archive_old_timeline_entries()` preserves
high-risk entries and instructions regardless of age — decay must never quietly
remove a safety-relevant item.

---

## 10. Trade-offs and what I would do next

**Extraction costs fluency.** Verbatim spans read less smoothly than a generated
summary. Accepted deliberately: a clinician who cannot verify a claim in seconds
will not trust it, and fluency is worth nothing without that.

**Over-redaction is the safe failure direction, but it is still a failure.** The
clinical allow-list covers ~70 common medications; an unusual drug name will be
redacted. Utility is lost, never privacy. A medical NER model would be the
principled fix.

**Confidence weights are reasoned, not fitted.** 0.50/0.35/0.15 is defensible but
unvalidated — there is no labelled outcome set here. The calibration harness
exists precisely so they can be refitted against real data rather than argued
about.

**Ensemble sampling costs N× tokens, and without it confidence is coarse.**
Single-shot falls back to a neutral 0.5 agreement prior, so a byte-exact claim
with no rule support scores exactly 0.60 — the abstention threshold — and
surfaces as medium. In that configuration abstention only fires for claims that
matched after normalisation (0.51). The signal is directionally right but not
discriminating; enabling sampling is what makes it so. This is asserted by test
rather than left as a footnote, so the limitation cannot be forgotten.

**The collab server needs secrets the demo environment lacks**, so real-time
editing degrades to "Local Only" with direct saves. Concurrency semantics are
proven by test rather than by the live socket: non-destructive merge across
sections, deterministic same-section resolution, and atomic version allocation
under a 10-thread concurrent test.

**Ambient voice is mock-first, and that is a real limitation as well as a
guardrail.** The diarization and confidence pathways are exercised against a
fixture, not against Scribe. The parsing is defensive on both shapes the SDK can
return, but the live path has not been run end to end — enabling it is one
environment variable and one `pip install`, and should be done once, on camera,
rather than in CI.

**Next, in order.** Fit the confidence weights against labelled outcomes, so
0.50/0.35/0.15 stops being reasoned and starts being measured. Replace the
medication allow-list with a clinical NER model. Wire `services/conflict.py`
(edit-precedence) into the collab path, which currently has 10 passing tests and
no caller because that path runs in TypeScript and degrades to "Local Only"
without secrets. Then diarization quality work — overlap handling and
code-switching — which the brief offers extra credit for and which only matters
once live transcription is running.

---

## 11. Verification

```bash
cd ai-service && .venv/bin/python -m pytest tests/ -v   # 442 passed
cd frontend && npx tsc --noEmit && npm run build
cd collab-server && npx tsc --noEmit
node scripts/measure_glance.mjs
```

| Suite | Tests | Proves |
|---|---|---|
| `test_rbac_scope` | 18 | role isolation, cross-clinic denial, cross-role writes |
| `test_revision_history` | 16 | version increment, revert restores state, metadata-only audit |
| `test_highlight_provenance` | 21 | pointer schema, span resolution, referential integrity |
| `test_concurrent_edits` | 19 | non-destructive merge, deterministic resolution, atomic versioning |
| `test_self_learning_importance` | 11 | learning moves scores; tenant isolation holds |
| `test_phi_redaction` | 41 | zero PHI leakage; clinical values preserved |
| `test_clinical_safety` | 64 | extraction, floors, abstention, conflicts, patient gate, feedback |
| `test_meta_rls_sanity` | 3 | guards against a green suite that proves nothing |
| `test_highlights_pipeline_safety` | 7 | the safety layer runs *inside the real route*, not just as modules |
| `test_adversarial_safety` | 53 | prompt injection, obfuscated contradictions, multicultural PHI, RLS boundary probes |
| `test_conflicts_endpoint` | 26 | `/api/ai/conflicts`, JWK-set selection, auth failure modes |
| `test_transcribe_endpoint` | 33 | ambient voice pipeline, payload limits, credit guardrails |

The suites build their own PostgreSQL cluster from the migration file and run as
a non-superuser. A superuser bypasses RLS, which would make every access-control
assertion pass while proving nothing — `test_meta_rls_sanity` exists to catch
exactly that, by asserting the same query returns different row counts per role.

### Verifying the deployment, not just the policies

The suite proves what the migration says. It cannot prove what is actually
running in production, and the two diverged once already.

`scripts/verify_patient_isolation.mjs` signs in as a real patient and a real
clinician against a live host and asserts through the **API** rather than the
rendered page — a UI assertion would have passed throughout the window the
assessment was exposed. Nine checks: zero rows from `care_note_assessments` by
table and by id, no severity or confidence grading left in `glance_cache`, no
high/critical timeline entry reachable, and the care-team control proving
clinician and staff access still works, so the checks cannot be satisfied by
breaking the care team instead.

`supabase/verify_grants.sql` audits grants, RLS state and policy count for every
table in `public`, enumerated from `pg_class` rather than named in a list — an
omitted table renders identically to a passing one, which is precisely how
`care_note_assessments` escaped an earlier grants review.

The two answer different questions and are easy to confuse. A grant is the
table-level door; RLS decides rows. `authenticated` holding SELECT on
`care_note_assessments` is correct and expected — the patient still receives
nothing, because no policy admits them. Only the API probe settles row
visibility, and only the SQL audit settles whether PostgREST can open the table
at all.

**Adversarial evaluation found nine further defects**, every one of which would
have shipped looking correct:

| Defect | Consequence |
|---|---|
| `K+ 6.4` / `K 6.4 mEq/L` missed by the potassium rule | shorthand is ubiquitous, so the rule mostly did not fire |
| Spelled-out dosages not normalised | "one hundred mg" vs "10mg" raised **no** contradiction |
| Single-letter given name after a title | "Mr K Lim" leaked to the LLM |
| CJK names | 陈美玲 leaked entirely — no coverage in `en_core_web_sm` |
| Names inside JSON / key-value payloads | `{"patient_name":"Alice Wong"}` leaked |
| Case-sensitive label matching | `Patient: Rajesh s/o Muthusamy` never matched |

All are fixed, each with a regression test. Label text is now preserved around
the redaction (group-scoped spans) so the clinical record is not damaged by the
fix.

**Two earlier bugs these tests caught in my own safety code**, both of which would have
shipped looking correct: `\b\d+\b` does not match `100` in `100mg`, so a
hallucinated dose passed the patient gate; and negation was checked only before a
finding, so `"anaphylaxis ruled out"` escalated to critical.

---

## 12. Security triage and declared safety boundaries

*(Requested as "Section 8"; §8 is already the clinical safety layer, so this
appends as §12.)*

An adversarial audit of the finished build — `OVERNIGHT_VULNERABILITY_ASSESSMENT.md`
— found one critical vulnerability and nine lower-severity items. This section
records the triage, and then states plainly which capabilities this system does
**not** have, because in a clinical tool an undeclared boundary is more dangerous
than a missing feature.

### 12.1 The zero-day: patient privilege escalation

`profiles` carried `FOR UPDATE USING (id = auth.uid())` with no `WITH CHECK` and
no column restriction. A row a patient may update was therefore a row they may
update *any column of* — including `role`, which every other policy keys on:

```sql
UPDATE profiles SET role='clinician' WHERE id = auth.uid();
```

Measured against a seeded cluster, one statement, same JWT:

| Table | before | after |
|---|---|---|
| `care_note_assessments` | 0 | 1 |
| `timeline_entries` (internal) | 0 | 7 |
| `comments` | 0 | 3 |
| `note_versions` | 0 | 4 |

This defeated the `care_note_assessments` split, the `visibility='internal'`
exclusion, and the patient-facing gate at once — every patient-isolation control
in the system, bypassed by one call that never touched a route handler.

**Why the test suite did not catch it.** Every RBAC test asserted what a *patient*
may read. None asserted that a patient cannot *stop being* a patient. A green
suite is evidence about the paths you thought to test, and this is the clearest
example in the build of that not being the same as security.

**Fix:** a `BEFORE UPDATE` trigger pinning `role` and `clinic_id`
(`supabase/migrations/20260902_pin_profile_identity.sql`), restricted to
`current_user IN ('authenticated','anon')` — the only two roles PostgREST maps a
JWT to. The first draft tested `current_setting('role') = 'service_role'`, which
reads `'none'` on the owner connection and would have blocked seeding while
appearing correct; a test now covers exactly that case.

### 12.2 Secondary remediation

| Finding | Fix |
|---|---|
| Five AI endpoints admitted `patient` | Tightened to clinician/staff/admin; drafting matched to who may send |
| No rate limiting | In-process sliding window; tightest on the unauthenticated surface |
| Four tables without a `RESTRICTIVE` tenant policy | Added — the second barrier, not the first |
| `interaction_log` client-forgeable | `BEFORE INSERT` trigger stamps identity, role and time server-side |
| Webhook replay unguarded | Bounded `update_id` idempotency check |
| Event-loop blocking on sign-in | `httpx.AsyncClient`, awaited |
| No React error boundary | `app/error.tsx`, `app/global-error.tsx` |
| `window.prompt` retraction | Reuses `DeferReasonDialog` |

Test suite grew **420 → 442**. Every fix is mutation-checked: the fix is
deliberately re-broken to confirm a test notices.

**One limitation stated rather than buried.** The rate limiter is in-process. On a
single replica it is a real bound; on N replicas an attacker gets N times the
allowance. A limiter that silently scales with replica count is worse than none,
because it is believed.

### 12.3 Declared boundaries — what this system does not do

These are omissions by decision, not by oversight. Each was scoped and rejected
for the same reason: **a rushed clinical feature is worse than a documented
absence**, because clinicians calibrate their trust to what they believe the
system does.

**Streaming real-consult audio — NOT BUILT.** Transcription is whole-file, after
the consult ends. An allergy mentioned at minute two is known at minute twenty.
This is an architectural stance, not a bug: streaming ASR needs partial-hypothesis
handling, and a partial transcript that says "no known allergies" before the
correction arrives is a worse artefact than a late-but-complete one.

**Medication and dosage confirmation — NOT BUILT.** Grounding proves a dose was
*said*. It does not prove the dose is *safe*. There is no formulary lookup, no
interaction check, no dose-range validation. The boundary is deliberate and
absolute: **the AI is not trusted to evaluate clinical safety.** It can show a
clinician what was said and where it came from; judging whether 100mg of
lisinopril is appropriate for this patient is the clinician's, and building a
half-formulary would invite exactly the delegation this design refuses.

**Audience-specific readability — NOT BUILT.** Patient-facing text is heavily
*gated* — grounding, prohibited speech acts, named human approval — but never
*rewritten*. A reading-level transform is a generative rewrite of clinical
content, and every rewrite is an opportunity to alter meaning: "take one tablet
twice daily" simplified into "take two tablets daily" is fluent, friendlier, and
wrong. The clinician's words go to the patient unchanged, or they do not go.

**Speaker attribution — PARTIAL.** ElevenLabs Scribe v2 diarizes, and the
transcript carries speaker labels through `Segment.speaker`. What is missing is
frontend UX: no speaker-identity mapping ("Speaker 1" is not resolved to
"Dr Tan"), no per-speaker filtering, no correction affordance. The data is
present and unexploited.

**Self-learning — PARTIAL, and the gap is the interesting part.** The loop is
clinic-scoped, bounded by an absolute floor (`critical` ≥ 0.90), and resistant to
alert fatigue: forty dismissals cannot bury an anaphylaxis flag. What it lacks is
**exposure-bias correction**. It only ever scores what it surfaced, so it grows
more confident about what it already believed and never learns that something it
suppressed deserved promotion. Correcting it needs deliberate random sampling of
suppressed items — cheap to describe, and not something to introduce untested
into a ranking that clinicians rely on. The `interaction_log` forgery fix in
§12.2 at least means the data feeding the loop is now server-asserted.

### 12.4 What would break first, today

Ranked honestly:

1. **The escalation, until the migration is applied to production.** The fix is
   committed; a migration in the repository protects nobody.
2. **Rate limiting under multiple replicas**, per the caveat above.
3. **`PatientWorkspace.tsx` at ~1800 lines.** Two production defects already
   traced to its size — the `glance_cache` write-back leak and two drifting URL
   builders. It is a known fault line, deliberately left alone this close to a
   deadline rather than refactored under time pressure.
