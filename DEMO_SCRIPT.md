# Nightingale — Demo Script

**Target: 4 minutes.** Recorded entirely on localhost. Every scenario demonstrated
is one the audit graded, and the two that fail are shown failing.

The through-line: *this system is built to be wrong safely.* Each segment shows a
guarantee, then shows what happens when it is stressed.

---

## Before you record

```bash
supabase start && ./scripts/seed.sh && npm run dev
```

Wait for `/ready` to report every check `true`:

```bash
curl -s http://localhost:8000/ready
```

Have three browser windows ready:

| Window | Signed in as |
|---|---|
| A | `clinician@nightingale.demo` |
| B | `patient@nightingale.demo` |
| C | `dr.miller@sunrise.demo` (other clinic) |

All passwords `demo-password-123`. Terminal visible for §1 and §5.

---

## 0:00 — 0:35 · It runs on your machine

**Screen:** terminal, nothing else.

```bash
supabase start
```

> "Nightingale runs entirely locally. This is Postgres, Auth, PostgREST and
> Studio in containers on my machine — no cloud account, no credentials of mine.
> The migrations apply automatically, including the row-level security policies,
> so a local instance is not a weakened one."

```bash
cd ai-service && .venv/bin/python -m pytest tests/ -q
```

> "480 tests. No credentials needed — the suite builds its own throwaway Postgres
> from the migration file and runs as a non-superuser, because a superuser
> bypasses RLS and every access-control test would pass while proving nothing."

**Do not skip this segment.** It is the claim everything else rests on.

---

## 0:35 — 1:15 · Glance View, and the thing that is not on it

**Screen:** Window A → patient record.

> "One shared note per patient. The Top Card is server-rendered from a
> denormalised cache — P95 79.7ms against a 300ms budget."

Point at the critical flags. Then switch to **Window B** (patient), same patient.

> "Same record, patient's own login. The care plan is here. The clinical
> assessment — the severity bands, the confidence scores, 'eGFR declining
> 62 to 45' — is not."

**Now show why that is real.** Terminal:

```bash
node scripts/verify_patient_isolation.mjs
```

> "Nine assertions, against the API rather than the page. That distinction
> matters: the first version of this fix hid the assessment in the component
> while leaving it readable by a direct API call. A UI test passed the entire
> time the data was exposed. So the assessment now lives in a table with no
> patient policy at all — not a filter the patient fails, the absence of any rule
> that could admit them."

**Scenario 12 · SURVIVES.**

---

## 1:15 — 1:55 · Two clinicians, one note

**Screen:** Windows A and C on the same note — or A twice if simpler.

Type in both. Save both.

> "With the collab server running, Yjs merges character by character — no lost
> update."

Now stop the collab server (`Ctrl-C` on `dev:collab`). Reload. Wait 5 seconds for
the amber badge.

> "Collab is down. The editor says **Local Only** — it does not pretend to be
> connected. Edits still save, straight to Supabase."

Type in both windows. Save both.

> "Second save is **refused**. 'SAVE BLOCKED: another user updated this note. Your
> draft has been preserved locally.' It does not retry with the fresh version —
> that is exactly the clobber this prevents — and it never clears the text box.
> That draft is the only copy in existence at that moment."

**Scenario 10 · SURVIVES.**

---

## 1:55 — 2:35 · Redaction, and proving the order

**Screen:** terminal split with the app.

> "PHI is stripped before any model call. The interesting part is that this is
> not enforced by ordering in the code — it is enforced structurally."

```bash
grep -n "assert_safe_for_model" ai-service/services/llm.py
grep -n "chat.completions.create" ai-service/services/llm.py
```

> "Line 127 is the guard. Line 134 is the only Groq call in the entire codebase.
> Every model call funnels through one function, and the guard re-reads the
> outgoing payload there. It **raises rather than repairs** — silently fixing a
> leaked field would hide the call path that skipped redaction."

Generate an AI summary in the app; click a highlight to jump to its source span.

> "Every highlight resolves to the exact words it came from."

**Scenarios 4 and 16 · SURVIVES / PARTIAL.** If a **[SOURCE EDITED — VERIFY
NOTE]** badge appears, name it:

> "That badge means the source moved after extraction. On seeded data it appears
> often, because only the scribe path writes the provenance hash. That is the
> safe direction — it never claims currency it cannot prove — but it is a real
> limitation, not a feature."

---

## 2:35 — 3:05 · When the model fails

**Screen:** Window A. Stop the AI service.

Trigger a summary.

> "Groq is unreachable. The clinician does not get a spinner and does not get an
> empty card — they get **Offline Mode, Rule-Derived**, with a line that matters:
> *absence of a flag does not imply absence of clinical concern.*"

> "An empty critical-flags panel reads as 'there are none'. Only a banner reads as
> 'this was not checked'. Those are opposite clinical actions, so the difference
> is spelled out in words rather than signalled by a colour."

> "Timeouts are layered: 20 seconds server-side, 25 in the browser. The server
> gives up first, so the client renders a real timeout instead of the request
> vanishing while work continues."

**Scenarios 8 · SURVIVES, 9 · PARTIAL** — say the second part out loud:

> "Partial, because the rule-derived panel is currently triggered by the
> contradiction check failing, not by every AI endpoint. One trigger surface out
> of four."

---

## 3:05 — 3:40 · The two that do not survive

**This is the most important segment. Do not rush it.**

**Screen:** timeline showing a nurse's penicillin note and an AI entry.

> "A nurse recorded a penicillin allergy. The patient told the AI they have no
> known allergies. Both are in the timeline. The contradiction count reads
> **zero**."

Pause on it.

> "This is graded **DOES NOT**, and I found it by writing the test, not by reading
> the code. The engine requires both sides to name the same drug with an explicit
> negation. 'Allergic to penicillin' versus 'not allergic to penicillin' is
> caught. 'Penicillin allergy' versus 'no known drug allergies' — the phrasing a
> patient actually uses — is not."

> "The engine deliberately requires explicit, matched drug negations rather than
> loose semantic matching. Loose matching in clinical UX produces high
> false-positive rates, and a contradiction flag that fires constantly is one
> clinicians learn to dismiss — which is the failure this whole system is built
> to avoid. But that reasoning does not make this case safe, and I have not
> pretended it does."

```bash
.venv/bin/python -m pytest tests/test_audit_boundaries.py -k blanket -v
```

> "That test asserts the gap exists. It fails the day someone fixes it, which
> forces the documentation to be regraded with the code. There are eighteen tests
> like it — they assert limitations, not features."

Then, briefly:

> "Second one: transcription is whole-file. An allergy stated at minute two of a
> consult is known at minute twenty. Streaming ASR needs partial-hypothesis
> handling, and a partial transcript claiming 'no known allergies' before the
> correction arrives is a worse artefact than a late but complete one. Not
> attempted, rather than half-built."

**Scenarios 13 and 7 · DOES NOT.**

---

## 3:40 — 4:00 · The boundary that matters most

**Screen:** the patient-message composer. Edit an AI draft to say `100mg` where
the record says `10mg`. Press Send.

Blocked, with `100000000mg`-style tokens highlighted.

> "The **edited** text is screened, at the moment of Send — not the AI's draft,
> because what the patient reads is the clinician's edit of it. The grounding
> sources are read server-side, so a fabricated dose cannot be supplied as its own
> evidence."

> "But this is a fidelity control, not a safety one, and that distinction is the
> line I want to end on. Grounding proves a dose was **said**. It does not prove
> the dose is **correct**. There is no formulary, no interaction check, no
> dose-range validation — deliberately. Deciding whether 10mg of lisinopril suits
> this patient needs a licensed drug database and the full medication history. A
> partial version covering twenty drugs would produce confident silence on the
> twenty-first, and silence from a system that usually flags things reads as
> approval."

> "The AI is not trusted to evaluate clinical safety. It shows a clinician what
> was said and where it came from. Judging it stays a human act — and the code is
> arranged so that cannot quietly change."

---

## Scenario coverage

| Segment | Scenarios | Grade shown |
|---|---|---|
| 0:35 Glance / isolation | 2, 12 | SURVIVES |
| 1:15 Concurrent editing | 10 | SURVIVES |
| 1:55 Redaction & provenance | 4, 16 | SURVIVES / PARTIAL |
| 2:35 Outage & timeout | 8, 9 | SURVIVES / PARTIAL |
| 3:05 Contradictions, streaming | 13, 7 | **DOES NOT** |
| 3:40 Dosage gate | 12, capability 5 | SURVIVES / MISSING |

Six SURVIVE · seven PARTIAL · three DO NOT — stated on camera, not buried.

---

## Recording notes

- **Record locally**, not against the hosted URL. Everything above works on
  `localhost:3000`; the deployment is secondary and the collab fallback is easier
  to demonstrate when you control the process.
- **Do not apologise for the Local Only badge or the Source Edited tag.** They are
  the product working. Say what each means and move on.
- Have `pytest` output already on screen before you start — waiting 40 seconds on
  camera is dead air.
- If the contradiction count shows a number instead of zero, the engine was
  improved after this script was written. Check `git log` on
  `clinical_conflict.py` and regrade scenario 13 before recording.
