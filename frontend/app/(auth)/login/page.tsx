'use client';

import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import { createClient } from '@/lib/supabase/client';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useNavigationProgress } from '@/lib/hooks/use-navigation-progress';
import type { UserRole } from '@/lib/types';
import {
  Activity,
  Stethoscope,
  ClipboardList,
  Users,
  Shield,
  Lock,
  Sparkles,
  Building2,
} from 'lucide-react';

interface DemoAccount {
  email: string;
  password: string;
  role: UserRole;
  name: string;
  description: string;
  icon: React.ElementType;
}

interface Clinic {
  id: string;
  name: string;
  shortName: string;
  accounts: DemoAccount[];
}

const clinics: Clinic[] = [
  {
    id: 'c0000000-0000-0000-0000-000000000001',
    name: 'Nightingale Family Clinic',
    shortName: 'Nightingale',
    accounts: [
      {
        email: 'clinician@nightingale.demo',
        password: 'demo-password-123',
        role: 'clinician',
        name: 'Dr. Sarah Chen',
        description: 'Full editor access, AI highlights, clinical decisions',
        icon: Stethoscope,
      },
      {
        email: 'staff@nightingale.demo',
        password: 'demo-password-123',
        role: 'staff',
        name: 'Nurse James Rivera',
        description: 'Staff notes, vitals tracking, compliance view',
        icon: ClipboardList,
      },
      {
        email: 'admin@nightingale.demo',
        password: 'demo-password-123',
        role: 'admin',
        name: 'Maria Santos',
        description: 'Read-only oversight, audit logging access',
        icon: Shield,
      },
    ],
  },
  {
    id: 'c0000000-0000-0000-0000-000000000002',
    name: 'Sunrise Medical Center',
    shortName: 'Sunrise',
    accounts: [
      {
        email: 'clinician2@nightingale.demo',
        password: 'demo-password-123',
        role: 'clinician',
        name: 'Dr. Michael Park',
        description: 'Full editor access, AI highlights, clinical decisions',
        icon: Stethoscope,
      },
      {
        email: 'staff2@nightingale.demo',
        password: 'demo-password-123',
        role: 'staff',
        name: 'Nurse Emily Torres',
        description: 'Staff notes, vitals tracking, compliance view',
        icon: ClipboardList,
      },
      {
        email: 'admin2@nightingale.demo',
        password: 'demo-password-123',
        role: 'admin',
        name: 'David Kim',
        description: 'Read-only oversight, audit logging access',
        icon: Shield,
      },
    ],
  },
];

export default function LoginPage() {
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [patientName, setPatientName] = useState('');
  const [showManual, setShowManual] = useState(false);
  const [selectedClinicIndex, setSelectedClinicIndex] = useState(0);
  const router = useRouter();
  const supabase = createClient();
  const { startNavigation } = useNavigationProgress();

  const selectedClinic = clinics[selectedClinicIndex];

  async function handleDemoLogin(account: DemoAccount) {
    startNavigation();
    setLoading(account.role);
    setError(null);
    try {
      const { error: signInError } = await supabase.auth.signInWithPassword({
        email: account.email,
        password: account.password,
      });
      if (signInError) throw signInError;
      router.push('/patients');
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Login failed';
      setError(message);
    } finally {
      setLoading(null);
    }
  }

  async function handleManualLogin(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    startNavigation();
    setLoading('manual');
    setError(null);
    try {
      const { error: signInError } = await supabase.auth.signInWithPassword({
        email,
        password,
      });
      if (signInError) throw signInError;
      router.push('/patients');
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Login failed';
      setError(message);
    } finally {
      setLoading(null);
    }
  }

  async function handlePatientLogin(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fullName = patientName.trim();
    if (!fullName || loading) return;

    startNavigation();
    setLoading('patient');
    setError(null);

    try {
      const res = await fetch('/api/auth/patient-login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ full_name: fullName }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(err?.error || 'Unable to find that patient');
      }

      const data = await res.json();
      const { error: signInError } = await supabase.auth.signInWithPassword({
        email: data.email,
        password: 'demo-password-123',
      });
      if (signInError) throw signInError;

      router.push('/patients');
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Patient login failed';
      setError(message);
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="min-h-screen bg-background flex flex-col">
      {/* Main content */}
      <div className="flex-1 flex flex-col items-center justify-center px-4 py-12 sm:px-6 md:px-10">
        <div className="w-full max-w-3xl space-y-10">
          {/* Hero section */}
          <div className="text-center space-y-4">
            <div className="flex items-center justify-center gap-4">
              <div className="w-12 h-12 sm:w-14 sm:h-14 rounded-xl bg-primary/10 flex items-center justify-center">
                <Activity className="w-7 h-7 sm:w-8 sm:h-8 text-primary" />
              </div>
              <h1 className="text-5xl sm:text-6xl font-bold text-foreground heading-display">
                Nightingale
              </h1>
            </div>
            <p className="text-xl sm:text-2xl text-muted-foreground font-medium">
              Shared care notes, <span className="text-primary">reimagined.</span>
            </p>
            <p className="text-muted-foreground text-base leading-relaxed max-w-lg mx-auto pt-2">
              A real-time collaborative patient note system with AI-powered insights,
              multi-role access, and complete provenance tracking.
            </p>

            {/* Feature pills */}
            <div className="flex flex-wrap items-center justify-center gap-3 pt-2">
              {[
                { icon: Sparkles, text: 'AI-powered highlights' },
                { icon: Users, text: 'Real-time collaboration' },
                { icon: Lock, text: 'Full audit trails' },
              ].map((feature) => (
                <div
                  key={feature.text}
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-secondary text-muted-foreground text-sm"
                >
                  <feature.icon className="w-3.5 h-3.5 text-primary" />
                  {feature.text}
                </div>
              ))}
            </div>
          </div>

          {/* Demo accounts section */}
          <div className="space-y-5">
            <p className="text-center text-sm font-medium text-muted-foreground">
              Try it now &mdash; pick a clinic and demo role
            </p>

            {/* Clinic selector */}
            <div className="flex items-center justify-center gap-2">
              <Building2 className="w-4 h-4 text-muted-foreground" />
              <div className="inline-flex rounded-lg border border-border p-1 bg-secondary/30">
                {clinics.map((clinic, idx) => (
                  <button
                    key={clinic.id}
                    onClick={() => setSelectedClinicIndex(idx)}
                    className={`px-4 py-2 text-sm font-medium rounded-md transition-all ${
                      selectedClinicIndex === idx
                        ? 'bg-card text-foreground shadow-sm'
                        : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    {clinic.shortName}
                  </button>
                ))}
              </div>
            </div>

            {/* Current clinic indicator */}
            <p className="text-center text-xs text-muted-foreground">
              Logging in to <span className="font-medium text-foreground">{selectedClinic.name}</span>
            </p>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4">
              {selectedClinic.accounts.map((account) => {
                const Icon = account.icon;
                const loadingKey = `${selectedClinic.id}-${account.role}`;
                return (
                  <button
                    key={loadingKey}
                    onClick={() => handleDemoLogin(account)}
                    disabled={loading !== null}
                    className={`group relative flex flex-col items-center text-center gap-3 p-4 sm:p-5 rounded-lg border border-border bg-card transition-all duration-200 hover:border-primary/30 hover:bg-primary/5 ${
                      loading === account.role ? 'opacity-70 scale-[0.99]' : ''
                    }`}
                  >
                    <div className="w-11 h-11 rounded-xl flex items-center justify-center bg-secondary text-muted-foreground group-hover:text-primary group-hover:bg-primary/10 transition-colors">
                      <Icon className="w-5 h-5" />
                    </div>
                    <div className="space-y-1.5">
                      <p className="text-sm font-semibold text-foreground whitespace-nowrap">
                        {account.name}
                      </p>
                      <Badge variant="outline" className="text-xs capitalize px-1.5">
                        {account.role}
                      </Badge>
                      <p className="text-xs text-muted-foreground leading-relaxed">
                        {account.description}
                      </p>
                    </div>
                    {loading === account.role && (
                      <div className="absolute inset-0 flex items-center justify-center bg-white/40 rounded-lg">
                        <div className="w-5 h-5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Patient quick login */}
          <div className="space-y-4">
            <p className="text-center text-sm font-medium text-muted-foreground">
              Patient view &mdash; enter your full name
            </p>
            <form onSubmit={handlePatientLogin} className="max-w-sm mx-auto space-y-3">
              <div>
                <label htmlFor="patient-name" className="text-sm font-medium text-foreground">
                  Full name
                </label>
                <input
                  id="patient-name"
                  type="text"
                  value={patientName}
                  onChange={(e) => setPatientName(e.target.value)}
                  className="w-full mt-1.5 px-4 py-2.5 bg-secondary/50 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
                  placeholder="Alice Wong or Bob Martinez"
                />
              </div>
              <Button type="submit" className="w-full h-11" disabled={loading !== null || !patientName.trim()}>
                {loading === 'patient' ? (
                  <span className="flex items-center gap-2">
                    <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    Signing in...
                  </span>
                ) : (
                  'Continue as Patient'
                )}
              </Button>
              <p className="text-xs text-muted-foreground text-center">
                Patients from any clinic can log in with their full name.
              </p>
            </form>
          </div>

          {/* Divider */}
          <div className="flex items-center gap-4">
            <div className="flex-1 h-px bg-border" />
            <button
              onClick={() => setShowManual(!showManual)}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              {showManual ? 'Hide' : 'Manual login'}
            </button>
            <div className="flex-1 h-px bg-border" />
          </div>

          {/* Manual login */}
          {showManual && (
            <form onSubmit={handleManualLogin} className="max-w-sm mx-auto space-y-4">
              <div>
                <label htmlFor="email" className="text-sm font-medium text-foreground">
                  Email
                </label>
                <input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full mt-1.5 px-4 py-2.5 bg-secondary/50 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
                  placeholder="you@clinic.com"
                />
              </div>
              <div>
                <label htmlFor="password" className="text-sm font-medium text-foreground">
                  Password
                </label>
                <input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full mt-1.5 px-4 py-2.5 bg-secondary/50 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all"
                  placeholder="Enter your password"
                />
              </div>
              <Button type="submit" className="w-full h-11" disabled={loading !== null}>
                {loading === 'manual' ? (
                  <span className="flex items-center gap-2">
                    <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    Signing in...
                  </span>
                ) : (
                  'Sign In'
                )}
              </Button>
              <div className="mt-4 p-3 rounded-lg bg-secondary text-xs text-muted-foreground leading-relaxed">
                <span className="font-medium text-foreground">Multi-clinic demo tip:</span>{' '}
                Use incognito windows to log in as users from different clinics simultaneously
                and see that they only have access to their own clinic&apos;s patients.
              </div>
            </form>
          )}

          {/* Error */}
          {error && (
            <div className="p-3.5 rounded-lg bg-red-50 border border-red-200/80 text-red-700 text-sm text-center">
              {error}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
