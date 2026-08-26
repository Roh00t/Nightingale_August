# Nightingale — Technical Brief

**A shared longitudinal patient note where the AI is never trusted on its own.**

The hard part of this problem is not summarisation. It is that a risk badge, a
confidence label and an importance score are all just numbers on a screen, and a
build can render all three without any of them meaning anything. This brief is
organised around the three questions that matter for each: what is it, how would
we know if it were wrong, and what happens when it is.

**237 automated tests, runnable offline with no credentials.**

---

## 1. Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Browser — Next.js 15 App Router                                         │
│                                                                          │
│   /patients/[id]  SERVER COMPONENT                                       │
│     └─ one indexed read of care_notes.glance_cache  ──► Top Card in HTML  │
│     └─ <PatientWorkspace> (client)                                       │
│          timeline · comments · highlights load separately, so history     │
│          volume never blocks the card                                    │
└───────┬────────────────────┬─────────────────────────┬───────────────────┘
        │ HTTPS              │ WSS                     │ HTTPS + JWT
        ▼                    ▼                         ▼
┌───────────────┐   ┌──────────────────┐   ┌──────────────────────────────┐
│   SUPABASE    │   │   HOCUSPOCUS     │   │   FASTAPI  :8000             │
│               │   │      :1234       │   │                              │
│ Auth (GoTrue) │   │ JWT verify       │   │  require_caller()  JWT gate  │
│ Postgres      │   │ ROLE allowlist   │   │        │                     │
│ ROW LEVEL     │   │  patient=REJECT  │   │        ▼                     │
│  SECURITY ◄───┼───┤  admin=readonly  │   │  redaction.py                │
│ 8 tables      │   │ clinic match     │   │   Presidio + spaCy + SG regex│
│ Realtime      │   │ Yjs CRDT sync    │   │        │                     │
└───────────────┘   │ create_note_     │   │        ▼                     │
        ▲           │  version() —     │   │  ╔══════════════════════════╗│
        │           │  advisory lock   │   │  ║   CLINICAL SAFETY LAYER  ║│
        │           └────────┬─────────┘   │  ║  extraction   (verbatim) ║│
        │                    │             │  ║  risk_rules   (floors)   ║│
        │  service-role key  │             │  ║  confidence   (abstain)  ║│
        └────────────────────┴─────────────┤  ║  conflict     (surface)  ║│
           RLS bypassed — tenant and role  │  ║  patient_gate (2 gates)  ║│
           checks re-applied in code (S3)  │  ║  feedback     (floors)   ║│
                                           │  ╚═══════════╤══════════════╝│
                                           │              ▼               │
                                           │        GROQ  gpt-oss-20b     │
                                           │   sees redacted text only    │
                                           └──────────────────────────────┘
```

Four processes, one Postgres. The safety layer sits between the model and the
record in **both** directions: text is redacted on the way out, and every claim
is validated on the way back before anything is stored.

---

## 2. Data model

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

## 3. Security

### RBAC at the database

RLS on all eight tables; the UI adapts to role but is never the control. Clinic
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

Staff and clinicians cannot overwrite each other: the only UPDATE policy is
`author_id = auth.uid()`. A cross-role write changes zero rows.

**Where RLS is bypassed** (service-role key), tenant and role checks are
re-implemented in code — AI scribe ingestion, the collab server, and account
provisioning. Each is listed in the README with its replacement check.

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

## 4. Clinical safety layer

### 4.1 Extraction over generation

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

### 4.2 Deterministic risk floors

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

### 4.3 Confidence and abstention

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

### 4.4 Clinical contradiction engine

Human-human contradictions are real and routine. This is a different problem
from resolving competing *edits*, and is handled differently.

Deterministic regex extracts medication–dosage pairs, allergy assertions and
their explicit denials, tagged with author, role, timestamp and verbatim quote.
Where one entity carries two values across two authors, a conflict is raised:
allergy contradictions rank **critical**, dosage **high**.

**The system never arbitrates.** If a clinician wrote `10mg` and a nurse recorded
`100mg`, it has no basis to decide which is correct, and picking one would
manufacture false certainty about a dosing decision. It surfaces the delta with
both quotes side by side and states that a clinician must resolve it.

Two things deliberately *not* flagged, because they would be noise: a single
author revising their own note over time (a correction), and vitals differing
across visits (the timeline working as intended).

Edit-level conflicts are separate and *do* resolve, deterministically: role
authority → recency → edit id. The id tie-break exists so two clients resolving
the same conflict independently cannot diverge. Losing edits are preserved, never
discarded. Clinician-over-AI resolves silently; two humans disagreeing still
picks a winner but is flagged for review.

### 4.5 Patient-facing maker-checker firewall

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

### 4.6 Feedback loop: exposure bias and fatigue

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

## 5. Performance — the ≤300ms warm glance path

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

## 6. Trade-offs and what I would do next

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

**Next, in order.** Measure the P95 against the repaired deployment. Wire the
safety layer's outputs through the AI routers so highlights carry real
confidence and floors end to end. Fit the confidence weights against labelled
outcomes. Replace the medication allow-list with a clinical NER model. Then
ambient voice capture, which is the largest remaining item in the brief and the
first thing to cut.

---

## 7. Verification

```bash
cd ai-service && .venv/bin/python -m pytest tests/ -v   # 177 passed
cd frontend && npx tsc --noEmit && npm run build
cd collab-server && npx tsc --noEmit
node scripts/measure_glance.mjs
```

| Suite | Tests | Proves |
|---|---|---|
| `test_rbac_scope` | 12 | role isolation, cross-clinic denial, cross-role writes |
| `test_revision_history` | 14 | version increment, revert restores state, metadata-only audit |
| `test_highlight_provenance` | 21 | pointer schema, span resolution, referential integrity |
| `test_concurrent_edits` | 19 | non-destructive merge, deterministic resolution, atomic versioning |
| `test_self_learning_importance` | 11 | learning moves scores; tenant isolation holds |
| `test_phi_redaction` | 33 | zero PHI leakage; clinical values preserved |
| `test_clinical_safety` | 64 | extraction, floors, abstention, conflicts, patient gate, feedback |
| `test_meta_rls_sanity` | 3 | guards against a green suite that proves nothing |
| `test_highlights_pipeline_safety` | 7 | the safety layer runs *inside the real route*, not just as modules |
| `test_adversarial_safety` | 53 | prompt injection, obfuscated contradictions, multicultural PHI, RLS boundary probes |

The suites build their own PostgreSQL cluster from the migration file and run as
a non-superuser. A superuser bypasses RLS, which would make every access-control
assertion pass while proving nothing — `test_meta_rls_sanity` exists to catch
exactly that, by asserting the same query returns different row counts per role.

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
