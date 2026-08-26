import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createClient } from '@supabase/supabase-js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, '..');

function loadEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return;
  const contents = fs.readFileSync(filePath, 'utf8');
  for (const line of contents.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eqIndex = trimmed.indexOf('=');
    if (eqIndex === -1) continue;
    const key = trimmed.slice(0, eqIndex).trim();
    const value = trimmed.slice(eqIndex + 1).trim().replace(/^"|"$/g, '');
    if (!process.env[key]) {
      process.env[key] = value;
    }
  }
}

loadEnvFile(path.join(repoRoot, '.env'));

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

if (!supabaseUrl || !serviceRoleKey) {
  console.error('Missing NEXT_PUBLIC_SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env');
  process.exit(1);
}

const supabase = createClient(supabaseUrl, serviceRoleKey);

const targets = [
  {
    email: process.env.DEMO_CLINICIAN_EMAIL || 'dr.chen@nightingale.demo',
    password: process.env.DEMO_CLINICIAN_PASSWORD || 'demo-clinician-2026',
  },
  {
    email: process.env.DEMO_STAFF_EMAIL || 'nurse.james@nightingale.demo',
    password: process.env.DEMO_STAFF_PASSWORD || 'demo-staff-2026',
  },
  {
    email: process.env.DEMO_PATIENT_EMAIL || 'alice.wong@nightingale.demo',
    password: process.env.DEMO_PATIENT_PASSWORD || 'demo-patient-2026',
  },
  {
    email: process.env.DEMO_ADMIN_EMAIL || 'maria.santos@nightingale.demo',
    password: process.env.DEMO_ADMIN_PASSWORD || 'demo-admin-2026',
  },
];

const { data, error } = await supabase.auth.admin.listUsers({ perPage: 1000 });
if (error) {
  console.error('Failed to list users:', error.message);
  process.exit(1);
}

const usersByEmail = new Map(
  data.users
    .filter((user) => user.email)
    .map((user) => [user.email.toLowerCase(), user])
);

for (const target of targets) {
  const user = usersByEmail.get(target.email.toLowerCase());
  if (!user) {
    console.warn(`No auth user found for ${target.email}`);
    continue;
  }

  const { error: updateError } = await supabase.auth.admin.updateUserById(user.id, {
    password: target.password,
    email_confirm: true,
  });

  if (updateError) {
    console.error(`Failed to update ${target.email}:`, updateError.message);
  } else {
    console.log(`Updated password for ${target.email}`);
  }
}
