# Nightingale — Demo Script

Three scenarios, roughly 8–10 minutes total. Each one demonstrates a guarantee
rather than a feature: what the system does when something is *wrong*.

---

## Record locally, not against the live URL

The deployment at **https://nightingale-august-frontend-6ktv.vercel.app** is the frontend against live Supabase. The FastAPI
AI service and the Hocuspocus collab server are **not deployed**, so on the live
host every AI moment in this script fails: no summaries, no highlights, no
contradiction badges, no ambient capture, and the editor stays on "Local Only".

Scenarios A and B both turn on those features, so **record against
`http://localhost:3000` with all three services running.** Use the live URL when
someone wants to click through the record and RBAC without installing anything.

## Before recording

```bash
# 1. Verify the whole suite is green — this is the claim the demo rests on
cd ai-service && .venv/bin/python -m pytest tests/ -q
# expect: 312 passed

# 2. Seed both clinics
cd .. && ./scripts/seed.sh

# 3. Production build (the latency claim is about the warm production path)
npm run build && npm start &

# 4. AI service
cd ai-service && .venv/bin/uvicorn main:app --port 8000 &

# 5. Capture the latency number on camera
node scripts/measure_glance.mjs
```

**Setup checklist**

- Browser zoom 110%, window 1440×900, DevTools closed except where noted.
- Two profiles or one normal + one incognito window, so a clinician and a
  patient session can be shown side by side without logging out.
- All accounts use `demo-password-123`.
- Have `supabase/migrations/001_foundation.sql` open in a tab for the RLS
  callout in Scenario B.

**Framing line to open with (~20s).**

> Clinicians trust AI up to a point. Everything here is built for what happens
> after that point — when the model is wrong, drifts, or invents something. The
> AI never gets the last word on anything a patient can see.

---

## Scenario A — Clinician workflow: glance, trace, resolve, approve

**Goal:** the Top Card is readable in under 10 seconds, every claim is traceable
to exact words, and the risk badge is not the model's opinion.

### A1. Glance View (60s)

1. Open **http://localhost:3000**, log in as `clinician@nightingale.demo`, and
   open **Alice Wong**.
2. Start a stopwatch as the page paints. Read aloud the three things you learn:
   - cardiology referral pending since Jan 15,
   - eGFR declining 62 → 45,
   - blood pressure improving 135/82 → 128/78.
3. Stop the stopwatch. **Say the number.** Target is under 10 seconds to
   actionable, not just painted.

> **Say:** "The Top Card is a single indexed read of a denormalised
> `glance_cache` column, server-rendered. The timeline loads separately, so how
> much history this patient has makes no difference to how fast this appears."

4. Cut to the terminal and show the measured figures from
   `scripts/measure_glance.mjs` — P50, P95, P99 and the sample size.

> **Say:** "That's a measured P95 over N sequential warm requests, not an
> estimate. Method's in the brief."

### A2. Sunshine disclosure block (50s)

1. Point at the block above the Top Card.

> **Say:** "Before any content, this answers three things: what needs doing, how
> much of this is the AI's opinion, and whether it's auditable. Open actions and
> critical flags up top. Then how many highlights are AI-derived, the mean
> measured confidence, how many claims the system *declined* to make, how many
> had their risk raised by a deterministic rule, and provenance coverage."

2. Hover **Mean confidence** and read the decomposition aloud.

> **Say:** "0.50 agreement across samples, 0.35 extraction verification, 0.15
> rule support. If no assessment was recorded it says 'not assessed' rather than
> showing a comforting default — an aggregate trust number is itself a number
> that can be wrong."

3. Hover **Authorship** — human versus system entry counts.

### A3. Click-to-trace — provenance (75s)

1. In the Top Card, click the **critical eGFR highlight**.
2. The timeline scrolls to the January 2026 lab entry and **the exact phrase
   flashes** inside the note.

> **Say:** "It didn't scroll to the note — it highlighted the words. That's the
> difference between a citation and a gesture at one. The AI is required to
> return exact substrings of the record; if a claim isn't a verbatim span of a
> source entry, it's rejected before it's ever stored. A paraphrase has no
> origin to check, so we don't accept paraphrase."

3. Hover the highlight's **confidence badge**. Show the tooltip decomposing the
   score: agreement across samples, extraction verification, rule support.

> **Say:** "Self-reported model confidence is decoration. This is measured.
> 'Medium' means 0.60 to 0.84 — the number is published, not vibes. Below 0.60
> the system abstains and sends the item to review rather than guessing."

### A4. Deterministic risk floor (60s)

> **Note:** conflict badges are demonstrated in Scenario B, where the
> contradiction is *created live*. The seeded data deliberately contains none —
> a clinician revising their own dose over time is a correction, not a
> disagreement, and the detector correctly ignores it.


1. Point at the **Rule floor** chip on the eGFR highlight. Hover it.

> **Say:** "The model proposed a lower level. A deterministic rule overrode it —
> eGFR at or below 45 is stage 3b. The model can raise risk. It can never lower
> it. `final = max(floor, model_proposal)`, so a prompt change or a model
> upgrade cannot quietly downgrade an anaphylaxis."

2. *(Optional, strong)* Cut to a terminal:

```bash
cd ai-service && .venv/bin/python -c "
from services.safety.risk_rules import assess_risk
for p in ['info','low','medium','high']:
    print(p, '->', assess_risk('Patient developed anaphylaxis', p).label)
print('negated:', assess_risk('Anaphylaxis ruled out').label)
"
```

> **Say:** "Every model proposal lands on critical. And 'ruled out' doesn't
> escalate — over-firing on negated findings is how you train a care team to
> ignore alerts."

### A5. Accept / reject and dismissal friction (45s)

1. Accept a highlight — one click.
2. Attempt to dismiss a **critical** item. Show that it demands a typed reason
   and cannot be bulk-dismissed.

> **Say:** "Low-risk noise is one click, because friction on noise is itself a
> fatigue driver. Critical items need a typed reason and can't be swept up in a
> bulk action. And interactions with critical items never train the model —
> critical classes are governed by deterministic floors, so letting them drift
> with usage would defeat the floor."

---

## Scenario B — Staff workflow and the enforcement boundary

**Goal:** show that access control is in the database, not the interface.

### B1. Staff view (45s)

1. Log in as `staff@nightingale.demo`. Open Alice Wong.
2. Add a timeline note: *"Vitals check: BP 130/80, HR 72. Patient reports
   sleeping better this week."*
3. Add a comment on the January lab entry with an `@clinician` mention.

### B2. Create a contradiction, live (90s)

1. As staff, add a second note, typed on camera:

   > *"Administered Lisinopril 100mg as charted. Chart says not allergic to
   > penicillin."*

2. Switch to the clinician window and reload Alice Wong.
3. The Sunshine block now shows **2 unresolved contradictions**. Click it.
4. Hover each conflict badge and show **both quotes side by side**:
   - **Dosage (high):** clinician `10mg` against staff `100mg`
   - **Allergy (critical):** clinician recorded *allergic to penicillin*, staff
     recorded *not allergic*

> **Say:** "Nobody told the system these disagree. It extracts medication-dose
> pairs and allergy assertions deterministically — regex, no model, so it can't
> invent a contradiction that isn't there. Two professionals disagree about a
> dose and about an allergy. It has no basis to decide who's right, and picking
> one would manufacture false certainty about a dosing decision. So it shows
> both, verbatim, with who said what, and says a clinician has to resolve it.
> Allergy ranks above dosage, because those are the ones that kill people."

5. Point out what is *not* flagged: the clinician raising Lisinopril from 5mg to
   10mg across two of their own notes.

> **Say:** "That's the same author correcting themselves over time. Flagging it
> would be noise, and noise is how you train a team to ignore alerts."

### B3. Cross-role write protection (60s)

1. Attempt to edit the clinician's January lab note. It is not editable.

> **Say:** "This isn't a disabled button. The only UPDATE policy on
> `timeline_entries` is `author_id = auth.uid()`. If staff sent that write
> directly to the API, it would change zero rows."

2. Show the policy in `001_foundation.sql`.

### B4. Prove it from outside the UI (75s)

```bash
cd ai-service && .venv/bin/python -m pytest tests/test_rbac_scope.py tests/test_meta_rls_sanity.py -v
```

Let the sanity output land on screen:

```
patient sees          1
clinician sees        8
sunrise clinician     3
service role         11   (RLS bypassed)
```

> **Say:** "Same query, four identities, four answers — from Postgres, not from
> the frontend. These tests build their own database from the migration file and
> run as a non-superuser, because a superuser bypasses RLS and every one of
> these assertions would pass while proving nothing."

### B5. Multi-tenant isolation (30s)

1. Log in as `dr.miller@sunrise.demo`. Only Robert Lee is visible.

> **Say:** "Two clinics exist so cross-tenant denial is testable rather than
> asserted. Even the self-learning signal is clinic-scoped — one clinic's pins
> provably don't move another clinic's scores. There's a test for exactly that."

---

## Scenario C — Patient view and the maker-checker firewall

**Goal:** the highest-liability surface, and the one place AI output cannot
reach its audience unaccompanied.

### C1. Patient isolation (60s)

1. In the second window, log in as `patient@nightingale.demo`.
2. Alice sees **only** her care instructions. Walk through what is absent:
   internal clinician notes, staff comments, AI-scribed summaries, highlights,
   version history.

> **Say:** "Patients can't see raw AI-scribed notes, and that's enforced twice —
> the entries are internal, *and* the patient's read policy excludes those entry
> types by name. If someone mis-marked an AI summary as patient-visible, it would
> still be hidden."

### C2. Patient submits an update (40s)

1. As Alice, send: *"I've been feeling dizzy in the mornings since last week."*
2. Confirm it appears in her own view.

> **Say:** "Patients can write, and read back what they wrote — a clause that
> was lost in the old migration chain and restored here. What they can't do is
> read anything else."

3. Switch to the clinician window; the message is in the timeline.

### C3. The firewall (2 min) — *the closing argument*

1. As the clinician, click **Message Patient**. The AI drafts a message.
2. **Edit the draft to say `100mg` instead of `10mg`.** Attempt to send.
3. The send is **blocked**, naming `100mg` as ungrounded.

> **Say:** "Every dose, number and drug name in a patient-facing message has to
> appear in the record. `100mg` doesn't. This is a string check, not a
> judgement, so the check itself can't hallucinate."

4. Try `10ml`. Blocked — wrong unit is a different token.

> **Say:** "Number-only matching would pass that. Ten milligrams and ten
> millilitres are not the same instruction."

5. Try *"You have chronic kidney disease and will not recover."* Blocked as a
   prohibited speech act.

> **Say:** "Diagnosis and prognosis are clinician speech acts. An assistant has
> no standing to make them to a patient."

6. Restore the correct draft. Show that a **passing draft still can't send**
   without approval, then approve as the clinician and send.
7. In the patient window, show the delivered message with its attribution line:
   *"drafted with AI assistance and reviewed and approved by Dr. Sarah Chen."*

> **Say:** "Three gates: grounded, permitted, and signed by a named human.
> Approval can't rescue a blocked draft — a clinician clicking approve on
> invented content is exactly what the first two gates exist to prevent. And the
> patient is told a human reviewed it, and who."

---

## Optional closer (60s)

> **Say:** "Two things I'd flag rather than hide. First, the self-learning loop
> only sees what it surfaced — if it scores something low and hides it, nobody
> corrects it, so the error is invisible and self-reinforcing. We sample 5% of
> unsurfaced items at random into a review queue, because that's the only way to
> measure a false negative. Second, confidence is only useful if it's
> calibrated: there's a Brier score and a per-band accuracy report, so if the
> system says 0.9 and is right 60% of the time, that shows up as a number rather
> than as a bad feeling."

---

## Recording notes

- **Show terminal output live.** The test run and the latency figures are the
  evidence; a claim about them is not.
- **Lead with failures.** The blocked sends and denied writes are more
  persuasive than the happy path.
- If a take is going long, cut A5 and the optional closer. Keep C3 — the
  firewall is the strongest two minutes in the build.
- Don't narrate the UI ("now I'm clicking…"). Narrate the guarantee.
