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

  // 1. Create auth user
  const { data: authData, error: authError } = await supabaseAdmin.auth.admin.createUser({
    email: uniqueEmail,
    password: 'demo-password-123',
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

  return NextResponse.json({
    id: patientId,
    display_name: trimmedName,
    email: uniqueEmail,
  }, { status: 201 });
}
