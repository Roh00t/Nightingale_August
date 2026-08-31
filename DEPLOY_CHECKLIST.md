# Pre-Panel Deployment Checklist

Everything below is **manual** — none of it can be done from the repo. Ordered by
dependency: each step assumes the previous one landed.

Current state: 398 tests pass, both TypeScript projects typecheck, `next build`
compiles, 1 commit(s) ahead of `origin/master`.

> **Secrets are never written into this file.** Read the generated webhook secret
> from the untracked root `.env` when you need to paste it:
>
> ```bash
> grep '^MESSAGING_WEBHOOK_SECRET=' .env
> ```

---

## 1. Database migrations — run in this order

Supabase Dashboard → **SQL Editor** → New query.

Easiest: paste **`supabase/DEPLOY_20260901_all.sql`** — all four in order, in one
run, verified to apply and re-apply cleanly against a throwaway cluster. Or
paste them individually below; the order is load-bearing either way.

| # | File | Adds | Depends on |
|---|---|---|---|
| 1 | `supabase/migrations/20260901_multi_clinic_rls.sql` | `clinic_id` on 5 tables, backfill trigger, RESTRICTIVE tenant policies | `001_foundation.sql` + `fix_live_grants.sql` |
| 2 | `supabase/migrations/20260901_care_notes_version.sql` | `care_notes.version`, `save_care_note_yjs()`, highlight provenance columns | 1 |
| 3 | `supabase/migrations/20260901_phone_identity_and_delivery.sql` | `profiles.phone_e164`, `patient_access_tokens`, `message_deliveries`, retraction columns | 1, 2 |
| 4 | `supabase/migrations/20260901_glance_cache_guard.sql` | Trigger preventing the assessment being written back into `glance_cache`, plus a repair of any row that already was | 1 |

**Why the order matters.** Migration 1 adds `clinic_id` as `NOT NULL` *after*
backfilling it from `care_notes`, and installs the trigger that keeps it correct
on every write. Migration 3 creates tables referencing `clinics(id)` and assumes
the tenant policies already exist. Running 3 first leaves rows the trigger never
filled.

**These have never run against real data.** They apply cleanly to a throwaway
cluster built from `001_foundation.sql`, and re-running them is safe, but
migration 1 adds NOT NULL columns with a backfill. If you have a Supabase branch,
run them there first.

**Verify after all four:**

```sql
-- Paste supabase/verify_grants.sql — expect every table true, RLS enabled.
```

```bash
node scripts/verify_patient_isolation.mjs
```

All 9 checks must pass.

---

## 2. Supabase Realtime for `care_notes`

Dashboard → **Database → Replication** → `supabase_realtime` publication →
enable **`care_notes`**.

What this does and does not do. Realtime respects RLS, so a subscriber receives
only rows they could already `SELECT` — the tenant policy from migration 1
applies to the stream too. It is **not** a substitute for the Hocuspocus collab
server: Realtime broadcasts row changes, not CRDT operations, so it gives you
neither live cursors nor character-level merge. With collab undeployed the editor
still shows **"Local Only"**, which is the designed fallback.

---

## 3. Production environment variables

### Railway — AI service

| Key | Value | Consequence if missing |
|---|---|---|
| `GROQ_API_KEY` | your Groq key | Every AI call fails; `/ready` shows `groq_api_key=false`; now logged explicitly |
| `SUPABASE_JWT_JWK` | the project's JWK set | **Currently missing.** Every AI endpoint returns `503 Authentication is not configured` |
| `SUPABASE_URL` | the Supabase project URL | Scribe ingestion and OTP lookups fail |
| `SUPABASE_SERVICE_ROLE_KEY` | service-role key | Gated patient-message writes fail |
| `OTP_PEPPER` | a **fresh** 32-byte hex | OTP issuance refuses rather than storing unpeppered hashes |
| `MESSAGING_PROVIDER` | `mock` | No provider: deliveries stay `queued` — honest, but nothing is sent |
| `MESSAGING_WEBHOOK_SECRET` | value from root `.env` | Webhook fails closed with 503; delivery status never advances |
| `AI_SERVICE_SELF_URL` | the Railway URL | Mock callback posts to localhost and never arrives |
| `CORS_ORIGINS` | *optional* | Unset uses the built-in list, which now includes the Vercel origin. Set it only to narrow or to admit a preview URL |

Generate the OTP pepper **separately** — reusing the webhook secret would make
one leak compromise both:

```bash
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
```

### Vercel — frontend

| Key | Value |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | the Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | anon/publishable key |
| `SUPABASE_SERVICE_ROLE_KEY` | service-role key (server-only, no `NEXT_PUBLIC_`) |
| `NEXT_PUBLIC_AI_SERVICE_URL` | the Railway URL |
| `NEXT_PUBLIC_COLLAB_URL` | **leave unset** — absent is what triggers the "Local Only" fallback |

**`NEXT_PUBLIC_*` is inlined into the client bundle at build time.** Editing one
in the dashboard changes nothing until you redeploy.

`GROQ_API_KEY` and `MESSAGING_WEBHOOK_SECRET` do **not** belong here — nothing in
the frontend reads them, and `NEXT_PUBLIC_`-prefixing either would publish it to
every visitor.

---

## 4. Redeploy Railway — the CORS fix lives in the service, not the frontend

The production origin was missing from `allow_origins`, so every browser call
from Vercel failed preflight with 400 and no `access-control-allow-origin`
header. The fix is in `ai-service/main.py`, so **Railway must redeploy** — a
Vercel build alone changes nothing.

Verify after it redeploys:

```bash
curl -s -o /dev/null -D - -X OPTIONS \
  https://nightingaleaugust-3zme-production.up.railway.app/api/ai/summarize \
  -H "Origin: https://nightingale-august-frontend-6ktv.vercel.app" \
  -H "Access-Control-Request-Method: POST" | grep -i access-control-allow-origin
```

Expect the Vercel origin echoed back. No header means it has not picked up the
change.

---

## 5. Push to trigger the Vercel build

Run the migrations first. The deployed frontend reads columns that migrations 2
and 3 create, so deploying ahead of them shows clinicians an empty Top Card.

```bash
git push origin master
```

---

## 6. Verify before the panel

```bash
curl -s https://nightingaleaugust-3zme-production.up.railway.app/ready
```

Every check must read `true` — **`jwt_verification` especially**. The service
reports `status: ready` when only Groq and redaction are healthy, so read the
individual fields, not the summary line.

```bash
node scripts/verify_patient_isolation.mjs
```

---

## Known gaps — state these rather than discover them on stage

- **No live messaging provider.** With `MESSAGING_PROVIDER=mock`, only the
  reserved range `+6580000001..4` is deliverable. Real numbers are refused by
  design, so a simulator can never contact a patient.
- **Collab server undeployed.** Hocuspocus holds an in-memory CRDT over a
  long-lived socket, which serverless cannot host. Demo "Local Only" as designed
  degradation — edits still persist to Supabase — or run co-editing locally.
- **Highlight provenance writes on the scribe path only.** Existing highlights
  have `source_note_version = NULL` and will render **"Source Modified"**. That is
  the safe direction — never a false claim of currency — but on seeded data the
  tag appears on everything.
