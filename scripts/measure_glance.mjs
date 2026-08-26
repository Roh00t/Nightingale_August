#!/usr/bin/env node
/**
 * Warm-path P95 measurement for the Glance View.
 *
 * The brief requires P95 <= 300ms for the consult glance view on a warm path,
 * and asks how it was measured. This measures it rather than asserting it.
 *
 * Method
 *   1. Authenticate against Supabase to obtain a session cookie, so the request
 *      exercises the real authenticated render path, not a redirect to /login.
 *   2. Send WARMUP requests that are discarded. "Warm path" means caches,
 *      connection pools and the Next.js route are already primed; including
 *      cold starts would measure the wrong thing, and excluding them without
 *      saying so would be dishonest.
 *   3. Send SAMPLES requests sequentially, timing to the last byte of HTML.
 *      Sequential, not parallel: concurrency measures throughput under load,
 *      which is a different claim than single-request latency.
 *   4. Report P50/P95/P99 with the sample size, so the number is reproducible.
 *
 * Usage
 *   node scripts/measure_glance.mjs [--url http://localhost:3000] [--n 50]
 */

import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");

function loadEnv() {
  const env = {};
  for (const file of [".env", "frontend/.env.local"]) {
    try {
      for (const line of readFileSync(resolve(ROOT, file), "utf8").split("\n")) {
        const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
        if (m && m[2]) env[m[1]] = m[2].replace(/^["']|["']$/g, "");
      }
    } catch { /* file is optional */ }
  }
  return env;
}

const args = process.argv.slice(2);
const argOf = (flag, fallback) => {
  const i = args.indexOf(flag);
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback;
};

const BASE = argOf("--url", "http://localhost:3000");
const SAMPLES = Number(argOf("--n", "50"));
const WARMUP = Number(argOf("--warmup", "10"));

const env = loadEnv();
const SUPABASE_URL = env.NEXT_PUBLIC_SUPABASE_URL;
const ANON_KEY = env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
const EMAIL = env.TEST_CLINICIAN_EMAIL || "clinician@nightingale.demo";
const PASSWORD = env.TEST_CLINICIAN_PASSWORD || "demo-password-123";

function percentile(sorted, p) {
  if (!sorted.length) return NaN;
  const idx = Math.ceil((p / 100) * sorted.length) - 1;
  return sorted[Math.min(Math.max(idx, 0), sorted.length - 1)];
}

async function signIn() {
  if (!SUPABASE_URL || !ANON_KEY) {
    console.error("Missing NEXT_PUBLIC_SUPABASE_URL / ANON_KEY in .env — cannot authenticate.");
    console.error("The glance route requires a session; without one this measures a redirect.");
    process.exit(1);
  }
  const res = await fetch(`${SUPABASE_URL}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: { "Content-Type": "application/json", apikey: ANON_KEY },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
  });
  if (!res.ok) {
    console.error(`Sign-in failed (${res.status}): ${await res.text()}`);
    process.exit(1);
  }
  return res.json();
}

async function resolvePatientId(session) {
  const res = await fetch(
    `${SUPABASE_URL}/rest/v1/care_notes?select=patient_id&limit=1`,
    { headers: { apikey: ANON_KEY, Authorization: `Bearer ${session.access_token}` } }
  );
  const rows = await res.json();
  if (!Array.isArray(rows) || !rows.length) {
    console.error("No care notes visible to this account — run ./scripts/seed.sh first.");
    process.exit(1);
  }
  return rows[0].patient_id;
}

async function timeOnce(url, cookie) {
  const started = performance.now();
  const res = await fetch(url, { headers: { cookie }, redirect: "manual" });
  await res.arrayBuffer(); // to last byte
  return { ms: performance.now() - started, status: res.status };
}

const session = await signIn();
const patientId = await resolvePatientId(session);
const url = `${BASE}/patients/${patientId}`;

// Supabase SSR stores the session in a project-scoped cookie.
const projectRef = new URL(SUPABASE_URL).hostname.split(".")[0];
const cookie = `sb-${projectRef}-auth-token=${encodeURIComponent(
  JSON.stringify([session.access_token, session.refresh_token, null, null, null])
)}`;

console.log(`\nGlance View warm-path latency`);
console.log(`  target   ${url}`);
console.log(`  warmup   ${WARMUP} discarded`);
console.log(`  samples  ${SAMPLES} sequential\n`);

for (let i = 0; i < WARMUP; i++) await timeOnce(url, cookie);

const timings = [];
let nonOk = 0;
for (let i = 0; i < SAMPLES; i++) {
  const { ms, status } = await timeOnce(url, cookie);
  if (status !== 200) nonOk++;
  timings.push(ms);
  process.stdout.write(`\r  measured ${i + 1}/${SAMPLES}`);
}
process.stdout.write("\n\n");

if (nonOk) {
  console.error(`  WARNING: ${nonOk}/${SAMPLES} responses were not 200 — the number below is not a valid render measurement.\n`);
}

const sorted = [...timings].sort((a, b) => a - b);
const mean = timings.reduce((a, b) => a + b, 0) / timings.length;
const p95 = percentile(sorted, 95);

const row = (k, v) => console.log(`  ${k.padEnd(10)} ${v.toFixed(1).padStart(8)} ms`);
row("min", sorted[0]);
row("mean", mean);
row("p50", percentile(sorted, 50));
row("p95", p95);
row("p99", percentile(sorted, 99));
row("max", sorted[sorted.length - 1]);

const pass = p95 <= 300;
console.log(`\n  P95 ${p95.toFixed(1)}ms vs 300ms budget: ${pass ? "PASS" : "FAIL"}  (n=${SAMPLES})\n`);
process.exit(pass ? 0 : 1);
