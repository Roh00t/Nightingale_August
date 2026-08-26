import { NextRequest, NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';

// Known patient emails for demo mode (fallback when service role key not available)
const DEMO_PATIENTS: Record<string, string> = {
  'Alice Wong': 'patient@nightingale.demo',
};

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => null);
  const fullName = body?.full_name?.trim();

  if (!fullName || typeof fullName !== 'string') {
    return NextResponse.json({ error: 'Full name is required' }, { status: 400 });
  }

  if (fullName.split(/\s+/).length < 2) {
    return NextResponse.json({ error: 'Please enter your full name' }, { status: 400 });
  }

  // Check for demo patient first (works without service role key)
  if (DEMO_PATIENTS[fullName]) {
    return NextResponse.json({ email: DEMO_PATIENTS[fullName] }, { status: 200 });
  }

  // For non-demo patients, we need the service role key
  const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!serviceRoleKey) {
    return NextResponse.json(
      { error: `Patient "${fullName}" not found. Try "Alice Wong" for the demo.` },
      { status: 404 }
    );
  }

  const supabaseAdmin = createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL || 'http://localhost:54321',
    serviceRoleKey,
  );

  const { data: profiles, error: profileError } = await supabaseAdmin
    .from('profiles')
    .select('id, display_name')
    .eq('role', 'patient')
    .eq('display_name', fullName)
    .limit(2);

  if (profileError || !profiles || profiles.length === 0) {
    return NextResponse.json({ error: 'No patient found with that full name' }, { status: 404 });
  }

  if (profiles.length > 1) {
    return NextResponse.json({ error: 'Multiple patients found. Please use the exact full name.' }, { status: 409 });
  }

  const profile = profiles[0];

  const { data: userData, error: userError } = await supabaseAdmin.auth.admin.getUserById(profile.id);
  if (userError || !userData?.user?.email) {
    return NextResponse.json({ error: 'Patient account missing email' }, { status: 400 });
  }

  const { error: updateError } = await supabaseAdmin.auth.admin.updateUserById(profile.id, {
    password: 'demo-password-123',
  });

  if (updateError) {
    return NextResponse.json({ error: 'Failed to prepare patient login' }, { status: 500 });
  }

  return NextResponse.json({ email: userData.user.email }, { status: 200 });
}
