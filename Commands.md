
---

## Bringing it up from cold

**Prerequisites** (one time):

```bash
brew install supabase/tap/supabase postgresql@17 && open -a Docker
```

```bash
sudo sysctl -w kern.sysv.shmall=65536 kern.sysv.shmmax=16777216
```

That sysctl does not survive a reboot. Without it the pytest harness dies with `postmaster became multithreaded during startup` while `pg_ctl` says only "could not start server".

**Boot:**

```bash
cd /Users/rohitpanda/Downloads/Nightingle/Nightingale_August && npx supabase start
```

```bash
./scripts/seed.sh
```

**Then refresh the JWK — this is the step that bites.** Local Supabase signs **ES256 with a `kid`**, and the key set changes whenever the stack is recreated. A stale JWK gives you a bare 401 on every authenticated endpoint while `/ready` still reports `jwt_verification: true`:

```bash
python3 - <<'PY'
import re, pathlib, urllib.request
j = urllib.request.urlopen("http://127.0.0.1:54321/auth/v1/.well-known/jwks.json").read().decode()
p = pathlib.Path(".env"); s = p.read_text()
p.write_text(re.sub(r"(?m)^SUPABASE_JWT_JWK=.*$", "SUPABASE_JWT_JWK=" + j, s))
print("JWK refreshed")
PY
```

**Start all three services:**

```bash
npm run dev
```

If you start them individually, **do not `source .env` first** — the mangled JWK is inherited and wins over the file.

**Verify:**

```bash
curl -s http://localhost:8000/ready && curl -s -o /dev/null -w '\nfrontend %{http_code}\n' http://localhost:3000/login
```

---

## Tests

```bash
cd ai-service && .venv/bin/python -m pytest tests/ -q
```

Expect **511 passed**. Prove it's genuinely offline:

```bash
cd ai-service && SUPABASE_URL=http://127.0.0.1:9 NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:9 GROQ_API_KEY=off .venv/bin/python -m pytest tests/ -q
```

```bash
cd frontend && npx vitest run && npx tsc --noEmit
```

**Never run `npm run build` while `next dev` is running** — it corrupts `.next` and every route 500s with a missing `routes-manifest.json`, which reads like a code fault. It bit me four times this session, including once just now. Recovery:

```bash
pkill -9 -f 'next dev|next-server'; rm -rf frontend/.next && npm run dev
```

## Demo accounts

All use `demo-password-123`, though the login page's role picker signs you in with one click: `clinician@nightingale.demo` (Dr. Sarah Chen), `staff@nightingale.demo`, `patient@nightingale.demo` (Alice Wong), `admin@nightingale.demo`. Sunrise clinic has its own four for testing tenant isolation — a Nightingale clinician opening a Sunrise patient correctly gets "No care note found."