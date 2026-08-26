'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { createClient } from '@/lib/supabase/client';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Skeleton } from '@/components/ui/skeleton';
import { TopBar } from '@/components/layout/TopBar';
import { useAppStore } from '@/lib/stores/app-store';
import { useNavigationProgress } from '@/lib/hooks/use-navigation-progress';
import type { CareNote, Profile } from '@/lib/types';
import { toast } from 'sonner';
import {
  Search,
  Plus,
  ArrowUpRight,
  Users,
  AlertTriangle,
  CheckCircle2,
  Loader2,
  X,
  Copy,
  ExternalLink,
} from 'lucide-react';

interface PatientWithNote {
  patient: Profile;
  careNote: CareNote | null;
}

export default function PatientsPage() {
  const [patients, setPatients] = useState<PatientWithNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [showNewPatient, setShowNewPatient] = useState(false);
  const [newPatientName, setNewPatientName] = useState('');
  const [creating, setCreating] = useState(false);
  const [createdPatient, setCreatedPatient] = useState<{ display_name: string; email: string; id: string } | null>(null);
  const router = useRouter();
  const supabase = createClient();
  const { currentUser } = useAppStore();
  const activeRole = currentUser?.role;
  const { startNavigation } = useNavigationProgress();

  useEffect(() => {
    async function loadPatients() {
      if (activeRole === 'patient' && currentUser) {
        const { data: note } = await supabase
          .from('care_notes')
          .select('*')
          .eq('patient_id', currentUser.id)
          .single();

        if (note) {
          router.push(`/patients/${currentUser.id}`);
          return;
        }
      }

      const { data: patientProfiles } = await supabase
        .from('profiles')
        .select('*')
        .eq('role', 'patient');

      if (patientProfiles) {
        const patientsWithNotes: PatientWithNote[] = await Promise.all(
          patientProfiles.map(async (patient) => {
            const { data: note } = await supabase
              .from('care_notes')
              .select('*')
              .eq('patient_id', patient.id)
              .single();
            return { patient: patient as Profile, careNote: note as CareNote | null };
          })
        );
        setPatients(patientsWithNotes);
      }
      setLoading(false);
    }

    loadPatients();
  }, [supabase, activeRole, currentUser, router]);

  async function handleCreatePatient(e: React.FormEvent) {
    e.preventDefault();
    if (!newPatientName.trim() || creating) return;

    setCreating(true);
    try {
      const res = await fetch('/api/patients', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display_name: newPatientName.trim() }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(err?.error || 'Failed to create patient');
      }

      const data = await res.json();
      setCreatedPatient({ display_name: data.display_name, email: data.email, id: data.id });
      setShowNewPatient(false);
      setNewPatientName('');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create patient';
      alert(message);
    } finally {
      setCreating(false);
    }
  }

  const filteredPatients = patients.filter((p) =>
    p.patient.display_name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  // Stats
  const totalPatients = patients.length;
  const criticalCount = patients.filter(
    (p) => p.careNote?.glance_cache?.top_items?.[0]?.risk_level === 'critical'
  ).length;
  const completedPlans = patients.filter(
    (p) => (p.careNote?.glance_cache?.care_plan_score || 0) >= 80
  ).length;

  if (loading) {
    return (
      <div>
        <TopBar user={currentUser} title="Patients" />
        <div className="p-4 sm:p-6 md:p-8 space-y-6">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-28 rounded-lg" />
            ))}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {[1, 2, 3, 4, 5, 6].map((i) => (
              <Skeleton key={i} className="h-40 rounded-lg" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div>
      <TopBar
        user={currentUser}
        title="Patients"
        subtitle={`${totalPatients} patient${totalPatients !== 1 ? 's' : ''} in your clinic`}
      />

      <div className="p-4 sm:p-6 md:p-8 space-y-6 min-w-[320px]">
        {/* Stats row */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <Card>
            <CardContent className="p-5">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                    Total Patients
                  </p>
                  <p className="text-3xl font-bold heading-display mt-1">{totalPatients}</p>
                </div>
                <div className="w-12 h-12 rounded-lg bg-primary/10 flex items-center justify-center">
                  <Users className="w-6 h-6 text-primary" />
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="p-5">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                    Critical Alerts
                  </p>
                  <p className="text-3xl font-bold heading-display mt-1 text-red-600">
                    {criticalCount}
                  </p>
                </div>
                <div className="w-12 h-12 rounded-lg bg-red-50 flex items-center justify-center">
                  <AlertTriangle className="w-6 h-6 text-red-500" />
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="p-5">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                    Plans Complete
                  </p>
                  <p className="text-3xl font-bold heading-display mt-1 text-primary">
                    {completedPlans}
                  </p>
                </div>
                <div className="w-12 h-12 rounded-lg bg-primary/10 flex items-center justify-center">
                  <CheckCircle2 className="w-6 h-6 text-primary" />
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Search & action bar */}
        <div className="flex items-center gap-2 sm:gap-3">
          <div className="flex-1 relative">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search patients..."
              className="w-full pl-10 pr-4 py-2.5 bg-card border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/40 transition-all"
            />
          </div>
          <Button className="h-10 gap-2 shrink-0" onClick={() => setShowNewPatient((v) => !v)}>
            {showNewPatient ? <X className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
            <span className="hidden sm:inline">{showNewPatient ? 'Cancel' : 'New Patient'}</span>
          </Button>
        </div>

        {/* New patient form */}
        {showNewPatient && (
          <Card>
            <CardContent className="p-5">
              <form onSubmit={handleCreatePatient} className="flex items-end gap-3">
                <div className="flex-1">
                  <label htmlFor="patientName" className="text-sm font-medium text-foreground">
                    Patient Name
                  </label>
                  <input
                    id="patientName"
                    type="text"
                    value={newPatientName}
                    onChange={(e) => setNewPatientName(e.target.value)}
                    placeholder="e.g. John Tan"
                    className="w-full mt-1.5 px-4 py-2.5 bg-secondary/50 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/40 transition-all"
                    autoFocus
                    disabled={creating}
                  />
                </div>
                <Button type="submit" className="h-10 gap-2" disabled={creating || !newPatientName.trim()}>
                  {creating ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Creating...
                    </>
                  ) : (
                    <>
                      <Plus className="w-4 h-4" />
                      Create Patient
                    </>
                  )}
                </Button>
              </form>
            </CardContent>
          </Card>
        )}

        {/* Created patient credentials card */}
        {createdPatient && (
          <Card className="border-primary/30 bg-primary/5">
            <CardContent className="p-5 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-foreground">Patient Created Successfully</h3>
                <button
                  onClick={() => setCreatedPatient(null)}
                  className="text-muted-foreground hover:text-foreground"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="space-y-2 text-sm">
                <div className="flex items-center justify-between py-1.5 border-b border-border">
                  <span className="text-muted-foreground">Name</span>
                  <span className="font-medium">{createdPatient.display_name}</span>
                </div>
                <div className="flex items-center justify-between py-1.5 border-b border-border">
                  <span className="text-muted-foreground">Email</span>
                  <span className="font-mono text-xs">{createdPatient.email}</span>
                </div>
                <div className="flex items-center justify-between py-1.5 border-b border-border">
                  <span className="text-muted-foreground">Password</span>
                  <span className="font-mono text-xs">demo-password-123</span>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                Open an incognito window and use Manual Login with these credentials.
              </p>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  className="gap-2"
                  onClick={() => {
                    const text = `Email: ${createdPatient.email}\nPassword: demo-password-123`;
                    navigator.clipboard.writeText(text);
                    toast.success('Credentials copied to clipboard');
                  }}
                >
                  <Copy className="w-3.5 h-3.5" />
                  Copy Credentials
                </Button>
                <Button
                  size="sm"
                  className="gap-2"
                  onClick={() => {
                    startNavigation();
                    router.push(`/patients/${createdPatient.id}`);
                  }}
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                  Go to Patient
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Patient grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {filteredPatients.map(({ patient, careNote }) => {
            const glance = careNote?.glance_cache;
            const topItem = glance?.top_items?.[0];
            const score = glance?.care_plan_score;
            const initials = patient.display_name
              .split(' ')
              .map((n) => n[0])
              .join('')
              .slice(0, 2);

            return (
              <Card
                key={patient.id}
                className="group cursor-pointer transition-all duration-200 hover:border-primary/20"
                onClick={() => {
                  startNavigation();
                  router.push(`/patients/${patient.id}`);
                }}
              >
                <CardContent className="p-5">
                  <div className="flex items-start gap-4">
                    <Avatar className="h-12 w-12 shrink-0">
                      <AvatarFallback className="bg-primary/10 text-primary font-bold text-sm">
                        {initials}
                      </AvatarFallback>
                    </Avatar>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between">
                        <h3 className="text-sm font-semibold text-foreground truncate">
                          {patient.display_name}
                        </h3>
                        <ArrowUpRight className="w-4 h-4 text-muted-foreground/0 group-hover:text-muted-foreground transition-all duration-200 shrink-0" />
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {careNote
                          ? `Last visit: ${glance?.last_visit || 'Unknown'}`
                          : 'No care note'}
                      </p>
                    </div>
                  </div>

                  {/* Risk highlight */}
                  {topItem && (
                    <div className="mt-4 flex items-center gap-2">
                      <Badge
                        variant={
                          topItem.risk_level as
                            | 'critical'
                            | 'high'
                            | 'medium'
                            | 'low'
                            | 'info'
                        }
                        className="text-xs shrink-0"
                      >
                        {topItem.risk_level.toUpperCase()}
                      </Badge>
                      <span className="text-xs text-muted-foreground truncate">
                        {topItem.text}
                      </span>
                    </div>
                  )}

                  {/* Care plan progress */}
                  {score !== undefined && (
                    <div className="mt-4">
                      <div className="flex items-center justify-between text-xs mb-1.5">
                        <span className="text-muted-foreground font-medium">Care Plan</span>
                        <span
                          className={`font-semibold ${
                            score >= 50 ? 'text-primary' : 'text-red-600'
                          }`}
                        >
                          {Math.round(score)}%
                        </span>
                      </div>
                      <div className="w-full bg-secondary rounded-full h-1.5 overflow-hidden">
                        <div
                          className={`h-1.5 rounded-full transition-all duration-700 ${
                            score >= 50 ? 'bg-primary' : 'bg-red-500'
                          }`}
                          style={{ width: `${Math.min(score, 100)}%` }}
                        />
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>

        {filteredPatients.length === 0 && (
          <Card>
            <CardContent className="py-16 text-center">
              <Users className="w-10 h-10 text-muted-foreground/30 mx-auto mb-3" />
              <p className="text-muted-foreground text-sm">
                {searchQuery
                  ? 'No patients match your search'
                  : 'No patients found in your clinic'}
              </p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
