'use client';

import React, { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { createClient } from '@/lib/supabase/client';
import { useAppStore } from '@/lib/stores/app-store';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Skeleton } from '@/components/ui/skeleton';
import type { Profile } from '@/lib/types';
import Link from 'next/link';
import { ChevronLeft, LogOut, Activity } from 'lucide-react';
import { getRoleColor } from '@/lib/utils';
import type { UserRole } from '@/lib/types';

export default function PatientLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const params = useParams();
  const patientId = params.id as string;
  const [patient, setPatient] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const supabase = createClient();
  const { setActivePatientId, currentUser } = useAppStore();

  const isPatientRole = currentUser?.role === 'patient';

  useEffect(() => {
    setActivePatientId(patientId);

    async function loadPatient() {
      const { data } = await supabase
        .from('profiles')
        .select('*')
        .eq('id', patientId)
        .single();

      if (data) setPatient(data as Profile);
      setLoading(false);
    }

    loadPatient();

    return () => setActivePatientId(null);
  }, [patientId, supabase, setActivePatientId]);

  if (loading) {
    return (
      <div className="p-8">
        <Skeleton className="h-16 w-full rounded-lg mb-4" />
        <Skeleton className="h-[600px] rounded-lg" />
      </div>
    );
  }

  const initials = patient?.display_name
    ?.split(' ')
    .map((n) => n[0])
    .join('')
    .slice(0, 2) || '??';

  async function handleLogout() {
    await supabase.auth.signOut();
    window.location.href = '/login';
  }

  const currentUserInitials = currentUser?.display_name
    ?.split(' ')
    .map((n) => n[0])
    .join('')
    .slice(0, 2) || '??';

  return (
    <div className="flex flex-col h-screen min-w-[320px]">
      {/* Main Header Bar */}
      <div className="flex items-center justify-between px-3 sm:px-6 h-12 border-b border-border bg-card shrink-0 gap-2">
        {/* Left: Logo + Back button + Patient name (on mobile) */}
        <div className="flex items-center gap-2 sm:gap-3 min-w-0 flex-1">
          <Link href="/patients" className="flex items-center gap-2 shrink-0">
            <div className="w-7 h-7 sm:w-8 sm:h-8 rounded-lg bg-primary/10 flex items-center justify-center">
              <Activity className="w-4 h-4 text-primary" />
            </div>
            <span className="font-bold text-sm heading-display hidden sm:inline">Nightingale</span>
          </Link>

          {!isPatientRole && (
            <>
              <div className="w-px h-5 bg-border hidden sm:block" />
              <Link
                href="/patients"
                className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors shrink-0"
              >
                <ChevronLeft className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Patients</span>
              </Link>
            </>
          )}

          {/* Patient name - inline on all screens */}
          {!isPatientRole && patient && (
            <div className="flex items-center gap-1.5 sm:gap-2 min-w-0 sm:absolute sm:left-1/2 sm:-translate-x-1/2">
              <Avatar className="h-5 w-5 sm:h-6 sm:w-6 shrink-0">
                <AvatarFallback className="bg-primary/10 text-primary font-bold text-[9px] sm:text-[10px]">
                  {initials}
                </AvatarFallback>
              </Avatar>
              <span className="text-xs sm:text-sm font-medium truncate">{patient.display_name}</span>
            </div>
          )}
        </div>

        {/* Right: User + Logout */}
        <div className="flex items-center gap-2 sm:gap-3 shrink-0">
          {currentUser && (
            <div className="flex items-center gap-2">
              <Avatar className="h-7 w-7 sm:h-8 sm:w-8">
                <AvatarFallback
                  style={{
                    backgroundColor: getRoleColor(currentUser.role as UserRole),
                    color: 'white',
                  }}
                  className="text-[10px] sm:text-xs font-bold"
                >
                  {currentUserInitials}
                </AvatarFallback>
              </Avatar>
              <div className="hidden md:block">
                <p className="text-sm font-medium text-foreground leading-tight">
                  {currentUser.display_name}
                </p>
                <p className="text-[11px] text-muted-foreground capitalize leading-tight">
                  {currentUser.role}
                </p>
              </div>
            </div>
          )}
          <button
            onClick={handleLogout}
            className="flex items-center gap-1.5 p-1.5 sm:px-3 sm:py-1.5 rounded-md text-xs font-medium text-muted-foreground hover:bg-red-50 hover:text-red-600 transition-colors"
            title="Log Out"
          >
            <LogOut className="w-3.5 h-3.5" />
            <span className="hidden md:inline">Log Out</span>
          </button>
        </div>
      </div>

      {/* Patient role context bar */}
      {isPatientRole && patient && (
        <div className="flex items-center gap-4 px-6 h-10 bg-secondary/30 border-b border-border shrink-0">
          <div className="flex items-center gap-2">
            <Avatar className="h-6 w-6">
              <AvatarFallback className="bg-primary/10 text-primary font-bold text-[10px]">
                {initials}
              </AvatarFallback>
            </Avatar>
            <span className="text-sm font-medium">Welcome, {patient.display_name}</span>
            <span className="text-xs text-muted-foreground">- Your Care Portal</span>
          </div>
        </div>
      )}

      {/* Page content */}
      <div className="flex-1 overflow-hidden">
        {children}
      </div>
    </div>
  );
}
