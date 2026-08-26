import { randomBytes } from 'node:crypto';
import { NextRequest, NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';
import { createClient as createServerClient } from '@/lib/supabase/server';

export async function POST(request: NextRequest) {
  // Check if service role key is available (required for creating patients)
  const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!serviceRoleKey) {
    return NextResponse.json(
      { error: 'Patient creation is disabled in demo mode. Use existing demo patients.' },
      { status: 503 }
    );
  }

  const supabaseAdmin = createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL || 'http://localhost:54321',
    serviceRoleKey,
  );

  // Verify the caller is authenticated
  const serverSupabase = await createServerClient();
  const { data: { user } } = await serverSupabase.auth.getUser();

  if (!user) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  // Verify caller is clinician or admin
  const { data: callerProfile } = await supabaseAdmin
    .from('profiles')
    .select('role, clinic_id')
    .eq('id', user.id)
    .single();

  if (!callerProfile || !['clinician', 'admin'].includes(callerProfile.role)) {
    return NextResponse.json({ error: 'Only clinicians and admins can create patients' }, { status: 403 });
  }

  const body = await request.json();
  const { display_name } = body;

  if (!display_name || typeof display_name !== 'string' || display_name.trim().length < 2) {
    return NextResponse.json({ error: 'Patient name is required (min 2 characters)' }, { status: 400 });
  }

  const trimmedName = display_name.trim();

  // Generate a unique email for the patient
  const slug = trimmedName.toLowerCase().replace(/\s+/g, '.').replace(/[^a-z0-9.]/g, '');
  const uniqueEmail = `patient-${slug}-${Date.now()}@nightingale.demo`;

  // 1. Create the auth user with a high-entropy password that is never
  //    persisted, logged, or returned.
  //
  //    This previously minted every new patient account with the hardcoded
  //    string 'demo-password-123'. It was gated behind a clinician/admin
  //    session, so not an authentication bypass — but every patient created
  //    through this route shared one guessable credential, and anyone who
  //    learned it could sign in as any of them. The account is now unusable
  //    until the patient sets their own password via the one-time link below,
  //    so no shared secret exists at any point.
  const throwawayPassword = randomBytes(32).toString('base64url');

  const { data: authData, error: authError } = await supabaseAdmin.auth.admin.createUser({
    email: uniqueEmail,
    password: throwawayPassword,
    email_confirm: true,
  });

  if (authError || !authData.user) {
    console.error('Failed to create auth user:', authError);
    return NextResponse.json({ error: 'Failed to create patient account' }, { status: 500 });
  }

  const patientId = authData.user.id;

  // 2. Create profile
  const { error: profileError } = await supabaseAdmin
    .from('profiles')
    .insert({
      id: patientId,
      clinic_id: callerProfile.clinic_id,
      role: 'patient',
      display_name: trimmedName,
    });

  if (profileError) {
    console.error('Failed to create profile:', profileError);
    // Clean up auth user
    await supabaseAdmin.auth.admin.deleteUser(patientId);
    return NextResponse.json({ error: 'Failed to create patient profile' }, { status: 500 });
  }

  // 3. Create care note
  const { error: noteError } = await supabaseAdmin
    .from('care_notes')
    .insert({
      patient_id: patientId,
      clinic_id: callerProfile.clinic_id,
      glance_cache: {
        top_items: [],
        care_plan_score: 0,
        last_visit: new Date().toISOString().split('T')[0],
      },
    });

  if (noteError) {
    console.error('Failed to create care note:', noteError);
    // Non-fatal — patient still usable, care note will be created on first visit
  }

  // 4. Issue a one-time link so the patient sets their own password.
  //    In production this is emailed to the patient; it is returned here
  //    because the demo environment has no SMTP configured. The caller is
  //    already an authenticated clinician or admin scoped to this patient's
  //    clinic, so they are entitled to hand it over.
  let setupLink: string | null = null;
  const { data: linkData, error: linkError } = await supabaseAdmin.auth.admin.generateLink({
    type: 'recovery',
    email: uniqueEmail,
  });

  if (linkError) {
    // Non-fatal: the account exists and is secure precisely because nobody
    // knows its password. The clinician can re-issue a link.
    console.error('Failed to generate patient setup link:', linkError.message);
  } else {
    setupLink = linkData?.properties?.action_link ?? null;
  }

  // The password is deliberately absent from this response and from every log.
  return NextResponse.json({
    id: patientId,
    display_name: trimmedName,
    email: uniqueEmail,
    setup_link: setupLink,
    setup_note: setupLink
      ? 'Share this one-time link with the patient so they can set their own password. It is not recoverable once used.'
      : 'Account created. Issue a password-reset link from the Supabase dashboard to complete setup.',
  }, { status: 201 });
}
