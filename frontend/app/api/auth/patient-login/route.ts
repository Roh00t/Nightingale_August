import { NextRequest, NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';
import { createClient as createServerClient } from '@/lib/supabase/server';

/**
 * Patient account lookup — care team only.
 *
 * SECURITY HISTORY: this route previously accepted an unauthenticated POST
 * carrying only a patient's full name, overwrote that account's password with a
 * hardcoded constant via `auth.admin.updateUserById`, and returned the account
 * email. Anyone who could guess a patient name obtained working credentials for
 * their record. It has been rewritten to satisfy guardrails.md S1 (no
 * unauthenticated write to auth state) and S3 (every service-role use
 * re-implements the tenant and role checks RLS would have applied).
 *
 * It now resolves a name to an account email for an authenticated clinician or
 * admin, scoped to the caller's own clinic. It never writes auth state.
 */
export async function POST(request: NextRequest) {
  // 1. Caller must be authenticated. Established before anything else runs.
  const serverSupabase = await createServerClient();
  const { data: { user } } = await serverSupabase.auth.getUser();

  if (!user) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!serviceRoleKey) {
    return NextResponse.json(
      { error: 'Patient lookup is unavailable in this environment.' },
      { status: 503 },
    );
  }

  const supabaseAdmin = createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL || 'http://localhost:54321',
    serviceRoleKey,
  );

  // 2. Caller must be clinician or admin. The service-role client bypasses RLS,
  //    so the clinic boundary is re-applied by hand below (S3).
  const { data: callerProfile } = await supabaseAdmin
    .from('profiles')
    .select('role, clinic_id')
    .eq('id', user.id)
    .single();

  if (!callerProfile || !['clinician', 'admin'].includes(callerProfile.role)) {
    return NextResponse.json(
      { error: 'Only clinicians and admins can look up patient accounts' },
      { status: 403 },
    );
  }

  const body = await request.json().catch(() => null);
  const fullName = typeof body?.full_name === 'string' ? body.full_name.trim() : '';

  if (!fullName) {
    return NextResponse.json({ error: 'Full name is required' }, { status: 400 });
  }

  if (fullName.split(/\s+/).length < 2) {
    return NextResponse.json({ error: 'Please enter the patient\'s full name' }, { status: 400 });
  }

  // 3. Search is confined to the caller's own clinic.
  const { data: profiles, error: profileError } = await supabaseAdmin
    .from('profiles')
    .select('id, display_name')
    .eq('role', 'patient')
    .eq('clinic_id', callerProfile.clinic_id)
    .eq('display_name', fullName)
    .limit(2);

  if (profileError || !profiles || profiles.length === 0) {
    return NextResponse.json({ error: 'No patient found with that full name' }, { status: 404 });
  }

  if (profiles.length > 1) {
    return NextResponse.json(
      { error: 'Multiple patients share that name. Use the patient list instead.' },
      { status: 409 },
    );
  }

  const { data: userData, error: userError } = await supabaseAdmin.auth.admin.getUserById(
    profiles[0].id,
  );

  if (userError || !userData?.user?.email) {
    return NextResponse.json({ error: 'Patient account is missing an email' }, { status: 400 });
  }

  // No password is read, written, or returned.
  return NextResponse.json(
    { id: profiles[0].id, display_name: profiles[0].display_name, email: userData.user.email },
    { status: 200 },
  );
}
