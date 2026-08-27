/**
 * Proves, against a live deployment, that a patient cannot read the clinical
 * risk assessment held about them.
 *
 * This exists because the leak it checks for was invisible from the UI. The
 * patient portal rendered only patient-safe fields, so the page looked correct
 * — but `care_notes.glance_cache` is a column on a row the patient owns, and
 * RLS is row-level, not column-level. Anyone holding a patient session could
 * skip the page entirely and read the clinician's judgement straight out of
 * PostgREST:
 *
 *     {"text": "eGFR declining: 62 -> 45 over 6 months",
 *      "risk_level": "critical", "confidence": 0.92}
 *
 * So this asserts against the API, not the rendered page. A UI test would have
 * passed the whole time the data was exposed.
 *
 *   node scripts/verify_patient_isolation.mjs
 *
 * Reads frontend/.env.local. Exits non-zero on any leak.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

function env(file, key) {
  const line = readFileSync(join(root, file), 'utf8')
    .split('\n')
    .find((l) => l.startsWith(`${key}=`));
  if (!line) throw new Error(`${key} missing from ${file}`);
  return line.slice(key.length + 1).trim();
}

const URL_ = env('frontend/.env.local', 'NEXT_PUBLIC_SUPABASE_URL');
const ANON = env('frontend/.env.local', 'NEXT_PUBLIC_SUPABASE_ANON_KEY');
const PASSWORD = 'demo-password-123'; // scripts/seed.sh is authoritative

async function signIn(email) {
  const res = await fetch(`${URL_}/auth/v1/token?grant_type=password`, {
    method: 'POST',
    headers: { apikey: ANON, 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password: PASSWORD }),
  });
  const body = await res.json();
  if (!body.access_token) {
    throw new Error(`sign-in failed for ${email}: ${body.error_description ?? body.msg}`);
  }
  return body.access_token;
}

async function rest(token, path) {
  const res = await fetch(`${URL_}/rest/v1/${path}`, {
    headers: { apikey: ANON, Authorization: `Bearer ${token}` },
  });
  return { status: res.status, body: await res.json() };
}

const failures = [];
const check = (ok, label, detail = '') => {
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? ` — ${detail}` : ''}`);
  if (!ok) failures.push(label);
};

const patient = await signIn('patient@nightingale.demo');
const clinician = await signIn('clinician@nightingale.demo');

console.log('\nAs the patient:');

// 1. The assessment table must be absent to them. Not filtered — absent.
const assess = await rest(patient, 'care_note_assessments?select=*');
check(
  assess.status === 200 && Array.isArray(assess.body) && assess.body.length === 0,
  'care_note_assessments returns zero rows',
  assess.status === 404
    ? 'table does not exist yet — apply supabase/fix_live_grants.sql'
    : `http ${assess.status}, ${JSON.stringify(assess.body).slice(0, 120)}`
);

// 2. The column they CAN read must hold nothing clinical.
const notes = await rest(patient, 'care_notes?select=glance_cache');
check(Array.isArray(notes.body) && notes.body.length > 0, 'patient still sees their own care note');

for (const row of notes.body ?? []) {
  const cache = row.glance_cache ?? {};
  check('care_plan_score' in cache, 'patient-safe fields survive', `keys: ${Object.keys(cache).join(', ')}`);
  check(!cache.top_items?.length, 'glance_cache carries no top_items',
    cache.top_items?.length ? JSON.stringify(cache.top_items[0]).slice(0, 90) : '');
  check(!cache.changes_since_last_visit?.length, 'glance_cache carries no changes_since_last_visit');

  // Belt and braces: catch the same assessment arriving under a different key.
  //
  // This looks for the SHAPE of a clinical judgement, not for clinical words. A
  // patient's own care plan may legitimately name their own labs — "Consider
  // nephrology consult if eGFR continues to decline" is an instruction written
  // for them, and a word-list scan would flag it. What must never cross is the
  // clinician's grading of them: a severity band, a model confidence, a triage
  // status. Those only ever appear as these keys.
  const graded = [];
  (function scan(node, path) {
    if (Array.isArray(node)) return node.forEach((v, i) => scan(v, `${path}[${i}]`));
    if (!node || typeof node !== 'object') return;
    for (const key of ['risk_level', 'confidence', 'severity', 'risk', 'status']) {
      if (key in node) graded.push(`${path}.${key} = ${JSON.stringify(node[key])}`);
    }
    for (const [k, v] of Object.entries(node)) scan(v, `${path}.${k}`);
  })(cache, 'glance_cache');

  check(graded.length === 0, 'no severity / confidence grading in glance_cache',
    graded.slice(0, 3).join('; '));
}

// 3. Nothing the patient can read describes them in internal severity terms.
const entries = await rest(patient, 'timeline_entries?select=entry_type,risk_level,visibility');
const severe = (entries.body ?? []).filter((e) => !['info', 'low'].includes(e.risk_level));
check(severe.length === 0, 'no high/critical timeline entry is patient-readable',
  severe.length ? JSON.stringify(severe[0]) : '');

console.log('\nAs the clinician (the control — access must be intact):');

const clinAssess = await rest(clinician, 'care_note_assessments?select=*');
check(clinAssess.status === 200 && (clinAssess.body ?? []).length > 0,
  'clinician can read the assessment', `http ${clinAssess.status}`);
check(!!clinAssess.body?.[0]?.assessment?.top_items?.length,
  'assessment payload still holds the risk items');

console.log(
  failures.length
    ? `\n${failures.length} check(s) failed.\n`
    : '\nAll checks passed — the assessment is unreachable by the patient.\n'
);
process.exit(failures.length ? 1 : 0);
