# Nightingale — Demo Day Runbook

Every command below is copy-pasteable. Every claim was verified against this
repo, not assumed.

## Deployment vs local

**Live:** https://nightingale-august-frontend-6ktv.vercel.app — frontend + Supabase. Good for handing someone a link to click
through sign-in, RBAC and the record.

**This runbook is for local**, because the AI service and collab server are not
deployed. Every AI feature and live-cursor step below requires all three
processes running on your machine. Do not substitute the live URL into these
commands.

## Read this first — four things that will break the demo

1. **Never `source .env`.** `SUPABASE_JWT_JWK` holds JSON; the shell strips its
   quotes and exports a corrupt value. Because a real environment variable beats
   the `.env` file, the AI service then 503s on every request and the collab
   server throws on the first WebSocket connection. Each service parses `.env`
   itself — just start it and let it.
2. **The collab server needs `SUPABASE_JWT_JWK`, not `SUPABASE_JWT_SECRET`.**
   This project's Supabase issues **ES256** tokens. The HS256 secret cannot
   verify them and every connection will be rejected. Both vars are set in
   `.env`; the JWK is checked first, so this works — as long as you don't
   corrupt it per (1).
3. **`npm run dev:ai` is broken.** It invokes a bare `uvicorn`, which resolves to
   a system Python without FastAPI. Start the AI service directly.
4. **The PWA cannot trigger a live ElevenLabs call.** The browser always sends
   the mock path — deliberate, so the UI cannot spend credits. The one live run
   goes through `curl` with `?live=true`. Recording the PWA and calling it "live"
   would be showing a fixture.

> There is **no local Postgres/Supabase to start.** This app runs against cloud
> Supabase. The ephemeral PostgreSQL cluster exists only inside the test suite,
> which builds and destroys it per run.

---

## 1. Full-stack startup sequence

Three services. Use **three separate terminals** — you want each log stream
visible on camera, and `npm run dev` hides which service failed.

| # | Service | Port | Health check | Blocks on |
|---|---|---|---|---|
| 0 | Supabase (cloud) | — | REST 200 | nothing |
| 1 | FastAPI AI service | 8000 | `GET /health`, `GET /ready` | Supabase |
| 2 | Hocuspocus collab | 1234 | TCP + `[auth]` log | Supabase |
| 3 | Next.js PWA | 3000 | `GET /login` | 1 and 2 |

**Order matters in one place only:** start the frontend **last**. The editor
gives the collab server a 5-second window before falling back to "Local Only".
If the frontend is up first, the badge latches to Local Only and only a page
reload clears it — which looks like a failure on camera.

### Step 0 — Preflight (30 seconds)

```bash
cd /Users/rohitpanda/Downloads/Nightingle/Nightingale_August
```

```bash
# Nothing already holding the ports
lsof -ti:3000,8000,1234
```

```bash
# Supabase reachable and seeded — expect HTTP 200
curl -s -o /dev/null -w "supabase: %{http_code}\n" \
  "$(grep -m1 '^SUPABASE_URL=' .env | cut -d= -f2-)/rest/v1/" \
  -H "apikey: $(grep -m1 '^SUPABASE_SERVICE_ROLE_KEY=' .env | cut -d= -f2-)"
```

If that returns `401`/`403`, run `supabase/fix_live_grants.sql` in the Supabase
SQL Editor, then `./scripts/seed.sh`.

### Step 1 — AI service (terminal 1)

```bash
cd /Users/rohitpanda/Downloads/Nightingle/Nightingale_August/ai-service
.venv/bin/uvicorn main:app --port 8000
```

Wait for `Application startup complete`. First boot takes ~15 s — it pre-warms
spaCy so the first request isn't penalised.

```bash
curl -s localhost:8000/ready | python3 -m json.tool
```

**All five must be `true`:**

```json
{ "status": "ready",
  "checks": { "groq_api_key": true, "supabase_url": true,
              "supabase_service_key": true, "jwt_verification": true,
              "redaction_engine": true } }
```

`redaction_engine: false` → `.venv/bin/python -m spacy download en_core_web_sm`.

### Step 2 — Collab server (terminal 2)

```bash
cd /Users/rohitpanda/Downloads/Nightingle/Nightingale_August/collab-server
npx tsx index.ts
```

Expect:

```
[nightingale-collab] Hocuspocus server running on port 1234
[nightingale-collab] WebSocket endpoint: ws://localhost:1234
```

If it exits immediately, see §2.

### Step 3 — Frontend (terminal 3, LAST)

```bash
cd /Users/rohitpanda/Downloads/Nightingle/Nightingale_August/frontend
npm run dev
```

```bash
curl -s -o /dev/null -w "frontend: %{http_code}\n" localhost:3000/login
```

Open **http://localhost:3000** — not `127.0.0.1`, not a LAN IP.
`getUserMedia` requires a secure origin; `localhost` qualifies and a LAN IP does
not, so voice capture will be silently refused anywhere else.

### Full stack health check

```bash
printf "ai:       %s\n" "$(curl -s -o /dev/null -w '%{http_code}' localhost:8000/health)"
printf "frontend: %s\n" "$(curl -s -o /dev/null -w '%{http_code}' localhost:3000/login)"
printf "collab:   %s\n" "$(nc -z localhost 1234 && echo open || echo CLOSED)"
```

### Shutdown

```bash
lsof -ti:3000,8000,1234 | xargs kill -9 2>/dev/null; echo "stopped"
```

---

## 2. Live cursors — fixing "Local Only"

### What the badge means

The editor shows a connection badge. Four states, from `CareNoteEditor.tsx`:

| Badge | Meaning |
|---|---|
| **Live** (green) | Handshake succeeded. Cursors and CRDT sync are active. **This is the goal.** |
| Connecting… (amber) | In progress; 5-second window |
| **Local Only** (amber) | No connection within 5 s. Edits still save directly to Supabase. |
| Offline (red) | Connected, then dropped |

### Required environment

Read from the **repo-root `.env`**, loaded by `collab-server/index.ts` via
`dotenv` — not from the shell.

| Variable | Required | Purpose |
|---|---|---|
| `SUPABASE_JWT_JWK` | **Yes** | **ES256 public key set. This is the one that works.** |
| `SUPABASE_JWT_SECRET` | Fallback only | HS256. **Cannot verify this project's tokens.** |
| `SUPABASE_URL` | Yes | Profile lookup |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Loads Yjs state, bypassing RLS |
| `HOCUSPOCUS_PORT` | No | Defaults to 1234 |

**Why the JWK and not the secret.** Verified against a live token:

```json
{ "alg": "ES256", "kid": "6722eb64-1b3d-4e87-a0c8-a340f0208aeb", "typ": "JWT" }
```

`ES256` is asymmetric. `SUPABASE_JWT_SECRET` is an HS256 shared secret and will
reject every token with a signature error. `auth.ts` checks the JWK first and
selects the key matching the token's `kid`, so a correct `.env` works — the
failure mode is corrupting it, not choosing wrongly.

### Verify the JWK is intact (do this before every demo)

```bash
cd /Users/rohitpanda/Downloads/Nightingle/Nightingale_August
python3 -c "
import json,re
v=[re.match(r'^SUPABASE_JWT_JWK=(.*)$',l.strip()) for l in open('.env')]
v=[m.group(1) for m in v if m][0]
d=json.loads(v)
ks=d.get('keys',[d])
print('JWK OK —', len(ks), 'key(s):', [k.get('kid') for k in ks])
"
```

Anything other than `JWK OK` means the value is corrupt — restore it from the
Supabase dashboard (**Settings → API → JWT Keys**) and **do not** re-export it
through the shell.

### Terminal handshake test

Run **while the collab server is up**, from the repo root:

```bash
cd /Users/rohitpanda/Downloads/Nightingle/Nightingale_August
node -e '
const {readFileSync}=require("fs");
const env={}; for(const l of readFileSync(".env","utf8").split("\n")){
  const m=l.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/); if(m&&m[2]) env[m[1]]=m[2];
}
(async()=>{
  const s=await(await fetch(env.SUPABASE_URL+"/auth/v1/token?grant_type=password",{
    method:"POST",headers:{apikey:env.NEXT_PUBLIC_SUPABASE_ANON_KEY,"Content-Type":"application/json"},
    body:JSON.stringify({email:"clinician@nightingale.demo",password:"demo-password-123"})})).json();
  if(!s.access_token) return console.error("SIGN-IN FAILED:",s);
  const rows=await(await fetch(env.SUPABASE_URL+"/rest/v1/care_notes?select=id&limit=1",
    {headers:{apikey:env.NEXT_PUBLIC_SUPABASE_ANON_KEY,Authorization:"Bearer "+s.access_token}})).json();
  const {HocuspocusProvider}=require("./node_modules/@hocuspocus/provider");
  const Y=require("./node_modules/yjs");
  const p=new HocuspocusProvider({url:"ws://localhost:1234",name:"care-note:"+rows[0].id,
    document:new Y.Doc(),token:s.access_token,
    onAuthenticated:()=>{console.log("HANDSHAKE OK — cursors will be live");process.exit(0);},
    onAuthenticationFailed:({reason})=>{console.error("REJECTED:",reason);process.exit(1);}});
  setTimeout(()=>{console.error("TIMEOUT — collab server not reachable on :1234");process.exit(1);},8000);
})();'
```

`HANDSHAKE OK` means cursors will be live in the browser.

### Browser confirmation (what to show on camera)

1. Open **http://localhost:3000**, sign in as `clinician@nightingale.demo`.
2. Open a patient. The editor badge must read **Live**, green.
3. Second browser (incognito), sign in as `staff@nightingale.demo`, same patient.
4. Type in one window — the coloured cursor and text appear in the other.
5. Collab server terminal logs both handshakes:

```
[auth] Dr. Sarah Chen (clinician) authenticated for care-note:<uuid> [write]
[auth] Nurse James Rivera (staff) authenticated for care-note:<uuid> [write]
```

Sign in as `admin@nightingale.demo` and the log reads `[read-only]` — admins get
oversight, not edit rights, enforced at the protocol level.

### If it still says "Local Only"

| Symptom | Cause | Fix |
|---|---|---|
| Collab server exits on start | Missing `SUPABASE_JWT_JWK`/`SECRET` | Check `.env`; restart without `source` |
| `SyntaxError ... JSON` on connect | JWK corrupted by the shell | Restore from dashboard; never `source .env` |
| `JWT verification failed` | HS256 secret used against ES256 token | Ensure `SUPABASE_JWT_JWK` is set — it takes precedence |
| `No key ... matches the token's kid` | Signing key rotated | Re-copy the JWK from the dashboard |
| Badge latched to Local Only | Frontend started before collab | Reload the page |
| `ECONNREFUSED :1234` | Collab not running | Start terminal 2 |

---

## 3. ElevenLabs — one live run, frugal protocol

Budget is 10,000 credits. This section spends **one** call.

### Why this is a terminal step, not a PWA step

`VoiceCapture.tsx` builds its request as
`/api/ai/transcribe?interaction_type=<role>` — it **never sends `live=true`**.
That is intentional: the browser cannot spend credits. So recording through the
PWA on camera would show a *mock* transcript while you narrate it as live.

**Do both, in this order:** demo the PWA for the user experience, then cut to a
terminal for the one real Scribe v2 call showing genuine diarization and
timestamps.

### Step 1 — Install the SDK (once)

```bash
cd /Users/rohitpanda/Downloads/Nightingle/Nightingale_August/ai-service
.venv/bin/pip install elevenlabs
```

### Step 2 — Enable live, in `.env` only

Edit `.env` with an editor. **Do not `export`** — that corrupts the JWK for any
service started from that shell.

`ELEVENLABS_API_KEY` is **already set** in this repo's `.env`.
`ELEVENLABS_LIVE_ENABLED` is **not present at all**, which is why everything
currently runs on the mock. Append it:

```bash
cd /Users/rohitpanda/Downloads/Nightingle/Nightingale_August
printf '\nELEVENLABS_LIVE_ENABLED=true\n' >> .env
grep '^ELEVENLABS_' .env
```

Restart the AI service (terminal 1) so it re-reads `.env`:

```bash
# Ctrl-C, then:
cd /Users/rohitpanda/Downloads/Nightingle/Nightingale_August/ai-service
.venv/bin/uvicorn main:app --port 8000
```

### Step 3 — Record a sample (~20 seconds, two voices)

Two people, or one person shifting tone. Include a name and an NRIC so
redaction is visible, and a clinical value so you can show it survived.

> **A:** "Good morning, can you confirm your name and IC?"
> **B:** "Alice Wong, S1234567D."
> **A:** "Your eGFR has dropped to 45 and potassium is 5.1. I'm increasing
> Lisinopril to 10 milligrams."

macOS:

```bash
cd /Users/rohitpanda/Downloads/Nightingle/Nightingale_August
```

```bash
# Record, then press Ctrl-C to stop
ffmpeg -f avfoundation -i ":0" -t 25 -ac 1 -ar 16000 /tmp/consult.m4a
```

No ffmpeg? Use QuickTime → File → New Audio Recording → save as `/tmp/consult.m4a`.

```bash
# Must be under 5 MB — the server rejects larger with 413
ls -lh /tmp/consult.m4a
```

### Step 4 — The single live call

```bash
cd /Users/rohitpanda/Downloads/Nightingle/Nightingale_August
```

```bash
SB_URL=$(grep -m1 '^SUPABASE_URL=' .env | cut -d= -f2-)
ANON=$(grep -m1 '^NEXT_PUBLIC_SUPABASE_ANON_KEY=' .env | cut -d= -f2-)
TOKEN=$(curl -s -X POST "$SB_URL/auth/v1/token?grant_type=password" \
  -H "apikey: $ANON" -H "Content-Type: application/json" \
  -d '{"email":"clinician@nightingale.demo","password":"demo-password-123"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
echo "token acquired: ${TOKEN:0:24}..."
```

```bash
curl -s -X POST \
  "http://localhost:8000/api/ai/transcribe?interaction_type=doctor_consult&live=true" \
  -H "Authorization: Bearer $TOKEN" \
  -F "audio=@/tmp/consult.m4a;type=audio/mp4" \
  | python3 -m json.tool
```

**Confirm it was genuinely live** — `"source"` must read `elevenlabs`:

```json
"transcription": { "source": "elevenlabs", "model_id": "scribe_v2",
                   "speaker_count": 2, "mean_confidence": 0.94 }
```

If it says `"source": "mock"`, the call did **not** reach ElevenLabs and no
credits were spent — `ELEVENLABS_LIVE_ENABLED` is not `true` in the running
process. Fix `.env` and restart the service.

The AI service log prints one line per metered call:

```
WARNING  METERED: calling ElevenLabs Scribe (184320 bytes). This spends credits.
```

### What to point at on camera

| Field | What it proves |
|---|---|
| `transcription.source: "elevenlabs"` | Genuinely live, not the fixture |
| `segments[].speaker` | Real Scribe v2 diarization |
| `segments[].start` / `.end` | Real timestamps |
| `segments[].confidence` | Per-segment ASR confidence |
| `redacted_transcript` | `<PERSON_1>`, `<NRIC_1>` — PHI gone before the LLM |
| `redaction.entity_counts` | What was removed, by type |
| `summary` | De-redacted, because it becomes the clinical record |
| `eGFR`, `45`, `Lisinopril` still present | Redaction did not destroy clinical signal |

### Step 5 — Disable immediately (do not skip)

```bash
cd /Users/rohitpanda/Downloads/Nightingle/Nightingale_August
```

```bash
python3 - <<'PY'
import re, pathlib
p = pathlib.Path('.env')
s = p.read_text()
if re.search(r'^ELEVENLABS_LIVE_ENABLED=', s, flags=re.M):
    s = re.sub(r'^ELEVENLABS_LIVE_ENABLED=.*$',
               'ELEVENLABS_LIVE_ENABLED=false', s, flags=re.M)
else:
    # Absent by default. A substitution alone would no-op here and leave
    # live transcription enabled, so append instead.
    s = s.rstrip('\n') + '\nELEVENLABS_LIVE_ENABLED=false\n'
p.write_text(s)
print('ELEVENLABS_LIVE_ENABLED=false')
PY
```

Restart the AI service so the change takes effect, then **prove it's off**:

```bash
curl -s -X POST \
  "http://localhost:8000/api/ai/transcribe?interaction_type=doctor_consult&live=true" \
  -H "Authorization: Bearer $TOKEN" \
  -F "audio=@/tmp/consult.m4a;type=audio/mp4" \
  | python3 -c "import sys,json;print('source =', json.load(sys.stdin)['transcription']['source'])"
```

Must print `source = mock`. Even with `?live=true`, the deployment switch is off,
so no credits can be spent. That is the two-key guardrail doing its job — and
it's worth showing on camera as a safety property.

```bash
grep '^ELEVENLABS_LIVE_ENABLED=' .env    # confirm: false
```

---

## Pre-demo checklist

```bash
cd /Users/rohitpanda/Downloads/Nightingle/Nightingale_August/ai-service && \
  .venv/bin/python -m pytest tests/ -q          # expect 398 passed
```

- [ ] 398 tests pass
- [ ] `/ready` — all five checks `true`
- [ ] Collab handshake test prints `HANDSHAKE OK`
- [ ] Editor badge reads **Live**, not Local Only
- [ ] Two browsers show each other's cursors
- [ ] Browser at `localhost:3000` (not a LAN IP) so the microphone works
- [ ] `ELEVENLABS_LIVE_ENABLED=true` only for the single run, then `false`
- [ ] Audio sample under 5 MB
- [ ] Terminals visible on camera — the `[auth]` and `METERED` lines are evidence
