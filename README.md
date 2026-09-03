# Nightingale — Shared Longitudinal Patient Note

A single shared care note per patient, collaborative across clinician, staff and
patient, augmented by AI that is **not permitted to be trusted on its own**.

The interesting part of this build is not the LLM. It is the layer that treats
the LLM as fallible: verbatim extraction instead of generation, deterministic
risk floors the model cannot lower, measured confidence with an abstention rule,
and a maker-checker firewall on anything a patient will read.

**480 automated Python tests, 0 failures · 50 Vitest tests · Glance P95 79.7 ms · suite runs offline with no credentials.**

> macOS: the Python suite passes cleanly only with raised SysV shared memory limits — see [System Prerequisites](#system-prerequisites--shared-memory-macos).

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

## Features & architecture

What the system does, and where each capability is enforced. Every row links to
the section that explains the mechanism rather than restating it.

### At a glance

| Capability | Enforced at | Detail |
|---|---|---|
| **Shared longitudinal care note** | Supabase + Yjs CRDT | one record per patient, collaborative across clinician, staff and patient |
| **Glance View** | Server Component, single indexed read | measured **P95 79.7 ms** against a 300 ms budget |
| **AI safety layer** | `ai-service/services/safety/` | [below](#ai-safety-layer) |
| **PHI redaction** | `services/redaction.py` + egress guard | [PHI redaction](#phi-redaction--strictly-ordered-and-structurally-enforced) |
| **RBAC / tenant isolation** | PostgreSQL RLS | [Multi-tenant isolation](#multi-tenant-isolation) |
| **Maker-checker firewall** | RLS + `/api/ai/send-patient-message` | [The maker-checker firewall](#the-maker-checker-firewall) |
| **Ambient voice capture** | ElevenLabs Scribe v2, mock-first | [Ambient voice capture](#ambient-voice-capture) |
| **Graceful degradation** | client-side, no server dependency | [below](#feature-degradation--local-only-mode) |

### Deployment topology

| Tier | Host | Detail |
|---|---|---|
| **Next.js 15** (App Router, RSC) | **Vercel**, `sin1` | `NEXT_PUBLIC_*` inlined at build time |
| **PostgreSQL**, **11** RLS-protected tables | **Supabase**, managed | Auth (GoTrue, ES256), RLS, Realtime |
| **FastAPI** AI service | **Railway**, Nixpacks | Groq · Presidio · ElevenLabs |
| **Hocuspocus** (Yjs CRDT) | **not deployed** | degrades to "Local Only" |

> **Table count.** Nine tables carry RLS in `001_foundation.sql`; the September
> migrations add `patient_access_tokens` and `message_deliveries`, both
> RLS-enabled, for **eleven**. Counted against the live database — earlier
> documentation saying nine predates those migrations.

### AI safety layer

The interesting part of this build is not the model. It is the layer that treats
the model as fallible.

- **Verbatim extraction over generation.** A highlight must quote a span that
  exists in the record. Paraphrase is the failure mode — it is where a plausible
  sentence that nobody wrote enters a clinical note, and it cannot be traced back
  to a source because there is no source.
- **Deterministic risk floors.** Rules the model cannot lower. If a potassium of
  6.4 is present, the finding is `critical` regardless of what the model
  proposed; `risk_floor` and `model_risk` are stored **separately** so a badge
  can always show which one set the level.
- **Measured confidence with abstention.**
  `0.50 × agreement + 0.35 × verification + 0.15 × rules`. Below **0.60** the
  claim is withheld for review rather than guessed — *except* critical findings,
  which surface flagged, because silently withholding a possible anaphylaxis is
  the worse failure.
- **Three quantities, never collapsed.** `importance_score` (queue position),
  `confidence_score` (reliability) and `risk_level` (severity) are separate
  columns. Rendering importance as confidence is the decoration failure: it looks
  like a trust signal while measuring queue position.
- **No invented percentage, ever.** Where confidence cannot be computed, the
  Sunshine block reads **"not assessed"** and the per-highlight
  `ConfidenceBadge` renders **nothing at all** rather than defaulting to a band.
  An abstained item is instead labelled as withheld-pending-review, so "no badge"
  and "withheld" stay distinguishable.

### Privacy & redaction

PHI is stripped **before** any Groq call, never after — and the ordering is
enforced structurally rather than by convention. Every model call funnels through
one chokepoint where `assert_safe_for_model()` re-reads the payload and **raises
rather than repairs**: silently fixing one leaked field would hide the call path
that skipped redaction.

Detection is Presidio + spaCy `en_core_web_sm` (English recognizers only),
layered with custom Singapore recognizers that off-the-shelf Presidio misses
entirely — **NRIC/FIN** including the 2022 **M** series, `+65` phone formats, and
local naming conventions (`bin` / `binte` / `s/o` / `d/o` / `a/l` / `a/p`, CJK
names, titled and labelled forms).

Logs are scrubbed independently, on the **root** logger, so uvicorn access lines
and third-party tracebacks are covered — those are the records most likely to
carry raw input.

### Access control

Enforcement is at the database. The UI adapts to role; it is never the control.

A patient cannot read internal clinical notes, raw AI-scribed entries, or the
internal assessment written about them. The last one is worth stating precisely,
because it is where this codebase learned its most expensive lesson: **RLS is
row-level, not column-level.** A patient-readable row exposes every column in it,
so hiding a field in a server component hides it from the page without
withholding it from the patient. The assessment therefore lives in
`care_note_assessments` — a table with **no patient policy at all** — and a
trigger additionally prevents it being written back into the patient-readable
`glance_cache`.

### Patient identity without an email

A patient who exists to the clinic as a phone number in a WhatsApp thread can
reach everything, and the mechanism is worth stating precisely because the
obvious version does not exist.

**Telegram cannot message a phone number.** A bot may only send to a `chat_id`,
and a chat_id exists only after the person opens the bot themselves — there is no
API to initiate contact. That is the consent model, not an obstacle, so the flow
is a link rather than an outbound send:

```
POST /api/auth/patient-link      (staff)
  └─→ t.me/<Bot>?start=<token>       shown at the desk, or sent over WhatsApp
  └─→ /portal/login?token=<token>    any browser — "on WhatsApp" ≠ "has Telegram"
        └─→ POST /api/auth/redeem-token → real GoTrue session
```

Only the SHA-256 hash of the token is stored. Every redemption failure —
expired, consumed, unknown, attempt-exhausted — returns one indistinguishable
message, because each distinction is an oracle for probing valid tokens. A token
minted against a non-patient profile is refused outright.

### KISS: degraded states are stated, never implied

Users here are exhausted and in pain. Every degraded state says what it means in
words, because the alternative is worse than useless:

| State | What the clinician sees |
|---|---|
| AI unavailable | **Offline Mode (Rule-Derived)** — *"Absence of a flag does not imply absence of clinical concern."* Renders on the AI failing, **independently of the contradiction count** |
| Save rejected (OCC) | **SAVE BLOCKED: Another user updated this note.** *Draft preserved locally.* |
| Retracted message — **patient** | Solid red **[WITHDRAWN BY CARE TEAM]**, struck through, *"Do not follow this message"*, verbatim reason |
| Retracted message — **clinician** | Muted collapsed line, expandable, struck through, reason retained — see [Retraction is split by role](#retraction-is-split-by-role) |
| Stale provenance | Solid orange **[SOURCE EDITED — VERIFY NOTE]** |

The governing case is the first row. An empty critical-flags panel reads as
*"there are none"*; only a banner reads as *"this was not checked"* — and those
are opposite clinical actions. Colour alone, icons alone and hover-to-reveal are
not used for any clinical state.

**A rejected save never clears the editor.** The clinician's draft is the only
copy in existence at that moment; destroying it to display an error turns a
recoverable conflict into data loss.

### Cognitive load: two columns, reserved red, and disclosure that cannot hide danger

The clinician workspace was a three-column grid with everything expanded at once.
The changes below target scanning cost specifically, and each one is constrained
by the rule above it — a UI that reduces clutter by hiding a critical finding is
a net clinical loss.

**Two columns, not three.** A locked clinical summary (`xl:col-span-4`) beside an
active workspace (`xl:col-span-8`). The summary scrolls independently rather than
moving with the note — "locked" means it does not follow the editor, not that it
cannot scroll, because clipped clinical content is an absence and absences are
what the rule above forbids.

**No tabs. `@radix-ui/react-tabs` was removed from `package.json` entirely.**
A tabbed Care Note / Care Plan was the obvious way to stop the clinician
scrolling past drafting tools to reach the regimen, and it was rejected twice
over:

- *Working memory.* The regimen is referenced **while** drafting. Mutual
  exclusion is the wrong model for two things you read together.
- *Data loss.* Radix unmounts inactive panels, and `CareNoteEditor` destroys its
  Hocuspocus provider on unmount ([`CareNoteEditor.tsx:166`](frontend/components/editor/CareNoteEditor.tsx)).
  With the collab server down — the realistic clinic case, the one the amber
  **"Local Only"** badge exists for — switching tabs would destroy the only copy
  of the clinician's text. A tab panel that may never unmount is not a tab; it is
  a worse split-pane with an extra click.

Instead the two sit **side by side**, and removing the tab removes the unmount
lifecycle from the picture entirely rather than defending against it. The editor
pane uses the browser's own `resize-x` drag handle: no dependency, no React
state. `VoiceCapture` stays permanently mounted for the same class of reason — it
releases the microphone on unmount, so anything that unmounts it silently kills
an ambient capture that cannot be re-recorded.

**Measure.** The editor set `max-w-none`, which explicitly *disables* the prose
measure; at eight columns that is a very long line. It is now `max-w-[68ch]`.

**Progressive disclosure, with a hard floor.** *Sunshine disclosure*, *At a
Glance* and *Changes Since Last Visit* are native `<details>` — no accordion
dependency, no state, and browser find-in-page still reaches text inside a closed
one. Two constraints make collapsing admissible at all:

- Every closed `<summary>` carries a **count** (`"3 findings · 1 critical"`, or
  `"not checked — AI unavailable"`). A closed section is an absence, and an
  absence must never read as *"there is nothing here"*.
- When [`hasActiveCriticalAlert`](frontend/lib/clinical_alerts.ts) is true, the
  section renders as a plain `<section>` with **no disclosure control at all** —
  deliberately not `<details open>`, which still has a triangle to click. A
  critical finding must not be collapsible even on purpose.

That predicate is the single point of failure for the whole decision, which is
why it is a tested module rather than an inline expression: 22 tests,
mutation-verified against three specific breaks including keying on
`is_accepted !== false` instead of `== null`.

**Red is reserved for active clinical danger** (`guardrails.md` UI-3). It was
not: an unticked care-plan checkbox rendered `border-red-400 bg-red-50`, *louder*
than the critical-flags panel's own `border-red-200/60 bg-red-50/50`. A clinician
scanning for danger met fifteen red boxes meaning "not ticked yet" before
reaching one meaning "eGFR is falling", and the cost of that lands on the one
alert that mattered.

Red now appears only on: critical flags, abnormal labs, the maker-checker gate
block, the patient's withdrawal notice, and the `destructive` button variant.
Incomplete is neutral; rejecting an AI suggestion is a muted outline, because
disagreeing with the model is not a clinical danger. `TopCard` — the Glance View
— now contains **zero** red; the genuine red it displays comes from the
`CriticalFlags` component it renders. Pinned by tests that assert both halves:
that the decorative uses are gone **and** that the genuine ones survive, so an
over-correction fails too.

<a id="retraction-is-split-by-role"></a>
**Retraction is split by role, and the split is the design.** A clinician
scrolling a timeline meets every withdrawal ever issued; rendering each as a red
slab is precisely how red stops working. A patient sees one message and has to be
stopped from acting on it.

| Audience | Treatment | Why |
|---|---|---|
| **Patient** | Full red block, `[WITHDRAWN BY CARE TEAM]`, *"Do not follow this message"*, struck through, verbatim reason | They acted on the dose. This is a countermand, not a status |
| **Clinician** | Muted collapsed line, expandable, struck through, reason retained | Volume. Never hidden and never silent — UI-1 forbids signalling by absence, not choosing a calmer colour |

A boundary test asserts **the split** rather than a global rule, so a future
"let us make these consistent" change fails loudly instead of silently
re-opening the leak from the other direction.

**The offline banner was decoupled from the conflict count** — the most important
fix in this work, and a live bug rather than a refactor. `TopCard` rendered
*"Offline Mode (Rule-Derived)"* **inside** `{conflictCount > 0 && …}`. When the AI
is unreachable the contradiction check never runs, so `conflictCount` is `0`, so
the banner never appeared **in the one scenario it exists for**. A clinician
looked at a clean Glance View during an outage and read it as *"nothing found"*
rather than *"not checked"* — opposite clinical actions.

It now renders on `aiDegraded` alone, and the rule-derived panel sits **outside**
every collapsible, because an outage that can be folded away is an outage nobody
sees. Two structural tests pin it: one that the banner precedes the conflict
block, one that it is not nested inside it — the first alone would pass if it were
re-nested the other way. Both were mutation-verified by restoring the original
nesting, and the fix was confirmed in a browser with the AI service stopped and
zero conflicts present.

### Feature degradation — "Local Only" mode

When the Hocuspocus collaborative server is unreachable, the editor waits 5 s,
then shows an amber **"Local Only"** badge and falls back to reading and writing
`yjs_state` **directly against Supabase**.

This is designed degradation, not a failure state. Edits are saved and survive a
reload; what is lost is live cursors and simultaneous co-editing, and the badge
says so rather than pretending to be connected. Optimistic concurrency covers
that window: a second clinician's save is **refused** rather than allowed to
silently erase the first.

The same principle runs through the AI paths — a timeout or a 503 renders a
rule-derived summary labelled **"Offline Mode (Rule-Derived)"**, because an empty
"critical flags" panel does not read as *"we could not check"*, it reads as
*"there are none"*.

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

### UI telemetry — PHI-safe by schema, not by promise

The progressive disclosure above is a **bet**: that non-critical summary detail
may sit behind a labelled count. Telemetry exists to falsify that bet rather than
argue about it — if clinicians expand *At a Glance* within three seconds of every
page load, default-closed is costing them time and the data says so.

**First-party and self-hosted, not a third-party analytics tool.** No BAA or DPA
exists with any vendor, and HIPAA and Singapore's PDPA both require one before
PHI-adjacent data leaves the boundary. A "PHI-safe payload" is not the same as
"not personal data": at single-clinic scale, one clinician on shift means one
session, and timing alone is re-identifying. A browser-side SDK would also be the
only unguarded egress path in a system where every model call funnels through one
chokepoint that raises rather than repairs. Accepted tradeoff: no funnels, no
cohorts, no session replay, and someone has to read the dashboard.

**A separate table from `interaction_log`, for a clinical-safety reason.** That
table feeds the self-learning importance loop, and `importance.py` scores each row
as `ACTION_TYPE_WEIGHTS.get(action_type, 0.3)` — an **unknown** action type
defaults to **+0.3, a positive engagement weight**. Writing `expand` / `collapse`
rows there would have the ranking model read UI fidgeting as clinical
endorsement. Worse, the loop reads only the **200 most recent rows**, so
high-frequency UI events would flush genuine accept/reject signal out of the
window entirely. A clinician toggling an accordion forty times in a shift would
both promote arbitrary highlights and erase the loop's actual evidence.

**What the schema makes impossible:**

| Control | Mechanism |
|---|---|
| No identity | `ui_telemetry` has **no `user_id`, `patient_id` or `care_note_id` column**. The brief asked for an ephemeral session id behind an audited mapping table; this never writes the mapping, so there is nothing to protect. `clinic_id` + `user_role` answers every dashboard question, and a row cannot be re-joined to a clinician even by an admin |
| No free text | `component_id` and `action` are `CHECK`-constrained allowlists. A drug name as a component id **fails the insert** rather than being stored. Ids are literals from a TypeScript union — never built from a prop, a label, or element text, which is the path by which PHI reaches an analytics column |
| No forged attribution | `clinic_id` and `user_role` are stamped by a trigger, so a client claiming to be an admin in another clinic is overwritten |
| Bounded dimensions | `dwell_ms` and `value_pct` are range-checked; the emitter clamps rather than drops, since a capped event still says *"they opened it"* |
| Append-only | No `UPDATE` or `DELETE` policy exists. A row that can be edited after the fact is not evidence |
| Read-restricted | Admins read their own clinic only, through views that set `security_invoker` — without which a view runs as its **owner** and hands every clinic's rows to any reader |

Verified against a live seeded database: an off-allowlist `component_id` is
rejected; a clinician claiming `admin` in another clinic has both columns
overwritten; an admin of clinic 1 sees their data and an admin of clinic 2 sees
zero rows through the same view; a clinician sees zero.

> **A trap worth knowing about.** Non-admins may **write** telemetry but not read
> it. `INSERT … RETURNING` therefore also performs a `SELECT`, fails the read
> policy, and the whole statement errors with *"new row violates row-level
> security policy"* — which reads like the write was rejected. In `supabase-js`
> that means `.insert(row)` and never `.insert(row).select()`. Since telemetry is
> fire-and-forget, adding `.select()` would break every write with **no visible
> symptom**, and an empty dashboard reads as *"nobody expands anything"* rather
> than *"nothing was ever recorded"*.

#### `SECURITY DEFINER` made two stamping triggers inert

Both `stamp_interaction_log()` and `stamp_ui_telemetry()` opened with:

```sql
IF current_user NOT IN ('authenticated', 'anon') THEN RETURN NEW; END IF;
```

intending *"if this is a service-role or owner call, leave the row alone"*. But
both are `SECURITY DEFINER`, and inside such a function **`current_user` is the
function owner, not the caller**. The condition was always true, the early return
always taken, and neither trigger ever stamped anything.

The consequence was on **`interaction_log`**, which was already live.
Demonstrated on a seeded database as an authenticated clinician:

```sql
INSERT INTO interaction_log (user_id, user_role, action_type, ..., target_metadata)
VALUES (auth.uid(), 'admin', 'accept', ...,
        '{"keywords":["x"],"secret_note":"Patient Alice Wong has HIV"}');

-- stored_role:     admin   -- a role the caller does not hold
-- stored_metadata: kept verbatim, free-text PHI included
```

So the metadata allowlist documented as *"server-side authority over what the
learning loop reads"* stripped nothing — arbitrary keys, including free text,
persisted in a column the PHI posture treats as metadata-only — and `user_role`
was caller-supplied, making the loop's view of who did what forgeable by its own
subjects. After the fix the same statement stores role `clinician` and metadata
`{"keywords": ["x"]}`.

**Two things this was *not*, stated because overclaiming a fix is its own
failure:**

- **Not a privilege-escalation vector.** Forging `user_role` in a log table
  grants no privileges; it poisons attribution and admits PHI. The actual
  privilege-escalation control — `pin_profile_identity_columns()`, which blocks a
  user changing their own `role` or `clinic_id` — uses the same guard but is
  **not** `SECURITY DEFINER`, so its `current_user` really is the caller and it
  works as documented. Confirmed via `pg_proc.prosecdef` rather than by
  resemblance.
- **Not a telemetry exposure.** `ui_telemetry` was created and fixed in the same
  commit; it never ran with the broken guard.

The fix guards on `auth.uid() IS NULL` instead. That asks the question actually
intended — *is there an end-user JWT behind this call?* — and is unaffected by
`SECURITY DEFINER`, because it reads the request GUC rather than the executing
role. Service-role and seed inserts still pass through untouched, which is what
the original guard was for.

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

## Running it locally

**This application is designed to run entirely on your machine.** The hosted
deployment is secondary; everything below runs against a local Supabase stack
with no cloud account and no credentials of ours.

### Prerequisites — install these first

| Requirement | Why | Check |
|---|---|---|
| **Docker Desktop** or **OrbStack**, running | The local Supabase stack runs in containers. Nothing else needs it. | `docker --version` |
| **Supabase CLI** | Boots Postgres, Auth (GoTrue), PostgREST and Studio locally. | `supabase --version` |
| **Node.js 20+** | Next.js 15 App Router. Verified on v24. | `node --version` |
| **Python 3.11+** | AI service. Verified on 3.14. | `python3 --version` |
| **PostgreSQL client tools** (`initdb`, `pg_ctl`) | *Tests only.* The suite builds its own throwaway cluster. | `brew install postgresql@14` |

```bash
# macOS
brew install supabase/tap/supabase postgresql@14
```

> Docker must be **running**, not merely installed. `supabase start` fails with a
> daemon-connection error otherwise, which reads like a CLI problem and is not.

---

### System Prerequisites — shared memory (macOS)

**macOS developers running the local Supabase stack alongside the Python pytest
suite must increase their SysV shared memory limits to prevent Postgres `initdb`
crashes during test execution.** Run:

```bash
sudo sysctl -w kern.sysv.shmall=65536 kern.sysv.shmmax=16777216
```

**before running the test harness.**

Why it is needed: the pytest suite builds its own throwaway PostgreSQL cluster
(`tests/support/pgharness.py`) rather than reusing the Supabase one, so that
access-control assertions run as a non-superuser — a superuser bypasses RLS and
every such assertion would pass while proving nothing.

macOS ships a SysV shared memory allowance of **1024 pages — 4 MB in total**,
which is below what `initdb` needs. This is not resource contention with the
Supabase stack: those containers have their own IPC namespace and consume no
host SysV segments at all, so `ipcs -m` reads empty while `initdb` still fails.
The host default is simply too small on its own. Raising it is a one-line fix
that the CLI cannot apply for you, because it needs `sudo`.

The suite reports:

```
child process exited with exit code 1
initdb: removing contents of data directory "/var/folders/.../ng-pgdata-XXXXXXXX"
RuntimeError  tests/support/pgharness.py:139
```

Read that carefully, because the failure mode is misleading in two ways:

- It surfaces as **dozens of `ERROR at setup`**, not as test failures. Every test
  in the seven suites that need a live cluster errors identically
  (`test_rbac_scope`, `test_revision_history`, `test_self_learning_importance`,
  `test_highlight_provenance`, `test_concurrent_edits`,
  `test_adversarial_safety`, `test_meta_rls_sanity`). The remaining ~397 tests
  pass, so the run looks like a large regression rather than a resource limit.
- It is **not persistent**. `sysctl -w` does not survive a reboot, so a suite
  that was green yesterday errors today with no change to the code. If a run
  suddenly produces a wall of `initdb` errors, check this before reading a diff.

Verify the current values:

```bash
sysctl kern.sysv.shmall kern.sysv.shmmax
```

To make it survive reboots, add the same two settings to `/etc/sysctl.conf`.
Linux and Windows/WSL are unaffected; their defaults are already sufficient.

---

### 1. Start the local control plane

```bash
supabase start
```

First run pulls several container images and takes a few minutes. It prints an
API URL, an anon key and a service-role key — **you will paste those into `.env`
in step 3.**

| Service | Local port |
|---|---|
| API / PostgREST / Auth | `54321` |
| PostgreSQL | `54322` |
| Studio (browse the DB) | `54323` |

`supabase start` applies everything in `supabase/migrations/` automatically, in
filename order. That includes the privilege-escalation fix
(`20260902000001_pin_profile_identity.sql`) and the tenant-isolation policies, so a
local instance is not a weakened one.

### 2. Install dependencies

```bash
npm install                              # workspaces: frontend + collab-server
```

```bash
cd ai-service
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

> **On the spaCy model.** `en_core_web_sm` is distributed as a wheel outside
> PyPI, so it is pinned by URL in `requirements.txt` rather than left to a
> separate `python -m spacy download` step. Without that pin, `pip install`
> completes successfully and the service starts with `redaction_engine: false` —
> a PHI pipeline that silently is not running. It is a declared dependency
> specifically so it cannot be forgotten.

### 3. Environment — two files, not one

```bash
cp .env.example .env                     # AI service + collab server
```

Paste the values `supabase start` printed:

```bash
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321
SUPABASE_URL=http://127.0.0.1:54321
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon key from supabase start>
SUPABASE_SERVICE_ROLE_KEY=<service_role key from supabase start>
SUPABASE_JWT_SECRET=<JWT secret from supabase start>
```

Then the frontend's own file — Next.js reads env from its project root, not the
repo root:

```bash
cat > frontend/.env.local <<'EOF'
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon key>
SUPABASE_SERVICE_ROLE_KEY=<service_role key>
NEXT_PUBLIC_AI_SERVICE_URL=http://localhost:8000
NEXT_PUBLIC_COLLAB_URL=ws://localhost:1234
EOF
```

`GROQ_API_KEY` is optional locally — see *Degradation* below.

### 4. Seed both clinics

```bash
./scripts/seed.sh
```

Creates 8 auth users across two clinics with password `demo-password-123`, and
seeds care notes, timeline entries, highlights and comments. Reads
`NEXT_PUBLIC_SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` from the root `.env`.

### 5. Run

```bash
npm run dev
```

Frontend `:3000` · collab `:1234` · AI service `:8000`. Open
**http://localhost:3000** and sign in as `clinician@nightingale.demo` /
`demo-password-123`.

Confirm the AI service is healthy:

```bash
curl -s http://localhost:8000/ready
```

Read the individual checks, not `status` — the service reports `ready` when Groq
and redaction are healthy, so `jwt_verification: false` passes the summary line
while breaking every authenticated endpoint.

### 6. Tests

```bash
cd ai-service && .venv/bin/python -m pytest tests/ -q       # expect 480 passed, 0 failures
#   macOS: requires the sysctl under System Prerequisites, or 83 of these
#   ERROR at setup on initdb rather than failing
```

```bash
.venv/bin/python -m pytest tests/test_audit_boundaries.py -v
```

The suite needs **no credentials and no running Supabase** — it builds its own
throwaway PostgreSQL cluster from `supabase/migrations/001_foundation.sql` and
runs as a non-superuser, because a superuser bypasses RLS and every
access-control assertion would pass while proving nothing.

```bash
npm run typecheck && cd frontend && npx next build
```

---

### Demo accounts

All seeded by `./scripts/seed.sh`, password `demo-password-123`.

| Email | Role | Clinic |
|---|---|---|
| `clinician@nightingale.demo` | clinician | Nightingale Family |
| `staff@nightingale.demo` | staff | Nightingale Family |
| `patient@nightingale.demo` | patient | Nightingale Family |
| `admin@nightingale.demo` | admin | Nightingale Family |
| `dr.miller@sunrise.demo` | clinician | Sunrise |
| `emma.wilson@sunrise.demo` | staff | Sunrise |
| `robert.lee@sunrise.demo` | patient | Sunrise |
| `michael.brown@sunrise.demo` | admin | Sunrise |

Two clinics exist so tenant isolation is demonstrable rather than asserted: sign
in as `dr.miller@sunrise.demo` and the Nightingale patient is not visible, at the
database level.

---

### Degradation — what happens when things are not configured

Verified by cloning this repository to an empty directory and running it with
**no `.env` at all**:

| Missing | Behaviour | Verified |
|---|---|---|
| Every environment variable | `next build` compiles; `next dev` serves `/login` with **200** and zero runtime errors | ✅ measured on a clean clone |
| `GROQ_API_KEY` | AI service boots; `/ready` reports `groq_api_key: false`; AI calls return a clear error and the UI shows **"Offline Mode (Rule-Derived)"** with rule-derived findings. The record is unaffected — it comes from the database, not the model. | ✅ |
| `ELEVENLABS_API_KEY` | Voice capture uses a deterministic mock transcript. Live transcription needs **two** switches (`ELEVENLABS_API_KEY` *and* `ELEVENLABS_LIVE_ENABLED=true`) so it cannot start spending credits by accident. | ✅ |
| `MESSAGING_PROVIDER` | No provider. Deliveries stay `queued` and render as *not confirmed* — the honest state. There is deliberately no default provider. | ✅ |
| `TELEGRAM_BOT_TOKEN` | Dispatch refuses rather than silently no-opping. | ✅ |
| `OTP_PEPPER` | OTP issuance **refuses** rather than storing unpeppered hashes. | ✅ |
| Collab server not running | Editor waits 5s, shows an amber **"Local Only"** badge, and persists edits directly to Supabase. Work is saved; live cursors are not available. | ✅ |

**Nothing in that table is a crash.** Every missing credential produces either a
labelled degraded state or an explicit refusal. That is deliberate: a clinical
tool that dies on a missing key during a consult is worse than one that says what
it cannot do.

> **Never `source .env`.** `SUPABASE_JWT_JWK` holds JSON; the shell strips its
> quotes, and a real environment variable beats the file — every AI endpoint then
> returns 503. Each service parses `.env` itself.

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
| `pytest` → dozens of `ERROR at setup`, `initdb: removing contents of data directory` | macOS SysV shared memory limit too low for `initdb` | `sudo sysctl -w kern.sysv.shmall=65536 kern.sysv.shmmax=16777216` — see [System Prerequisites](#system-prerequisites--shared-memory-macos). Not persistent across reboots. |
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
npm test                     # 442 pytest tests (uses ai-service/.venv)
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

> **Frontend: 50 Vitest tests, and a deliberate boundary.**
>
> ```bash
> cd frontend && npx vitest run          # 50 passed
> ```
>
> These cover the *pure clinical rules* that were extracted out of components
> precisely so they could be tested — `patient_visibility` (what a patient may be
> shown), `clinical_alerts` (when a summary may not be collapsed), `care_plan`
> (the index contract behind a checkbox), `clinical_values`, `telemetry`. Several
> were mutation-tested: the fix is deliberately broken again to confirm a test
> notices.
>
> **ABSENT: any test that renders a component.** `vitest.config.ts` scopes the
> glob to `lib/` on purpose — there is no jsdom or Testing Library setup, so
> nothing here mounts the patient portal or the clinician workspace. What is
> proven is that the rules are correct, not that a browser honours them. The
> remaining coverage is `tsc --noEmit`, the production build, and the backend
> suites that exercise the same API contracts.

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
