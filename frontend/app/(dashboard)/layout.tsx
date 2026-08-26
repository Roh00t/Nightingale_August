'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { createClient } from '@/lib/supabase/client';
import { AppSidebar } from '@/components/layout/AppSidebar';
import { useAppStore } from '@/lib/stores/app-store';
import type { Profile } from '@/lib/types';
import { Activity } from 'lucide-react';

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const supabase = createClient();
  const { currentUser, setCurrentUser, sidebarOpen, activePatientId, setLogoutHandler } = useAppStore();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadUser() {
      const { data: { user } } = await supabase.auth.getUser();
      if (!user) {
        router.push('/login');
        return;
      }

      const { data: profile } = await supabase
        .from('profiles')
        .select('*')
        .eq('id', user.id)
        .single();

      if (profile) {
        setCurrentUser(profile as Profile);
      }
      setLoading(false);
    }

    loadUser();

    const { data: { subscription } } = supabase.auth.onAuthStateChange((event) => {
      if (event === 'SIGNED_OUT') {
        setCurrentUser(null);
        router.push('/login');
      }
    });

    return () => subscription.unsubscribe();
  }, [supabase, router, setCurrentUser]);

  async function handleLogout() {
    await supabase.auth.signOut();
    router.push('/login');
  }

  // Register logout handler for TopBar access
  useEffect(() => {
    setLogoutHandler(handleLogout);
    return () => setLogoutHandler(null);
  }, [setLogoutHandler]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="text-center space-y-4">
          <div className="w-14 h-14 rounded-lg bg-primary/10 flex items-center justify-center mx-auto">
            <Activity className="w-7 h-7 text-primary animate-pulse" />
          </div>
          <div>
            <h2 className="text-lg font-semibold heading-display text-foreground">Nightingale</h2>
            <p className="text-sm text-muted-foreground mt-1">Loading your workspace...</p>
          </div>
        </div>
      </div>
    );
  }

  // Sidebar removed - user info and logout now in TopBar
  const showSidebar = false;

  return (
    <div className="min-h-screen flex bg-background">
      {showSidebar && (
        <AppSidebar
          user={currentUser}
          onLogout={handleLogout}
        />
      )}
      <main
        className={`flex-1 min-h-screen transition-sidebar ${
          showSidebar ? (sidebarOpen ? 'ml-[240px]' : 'ml-[68px]') : 'ml-0'
        }`}
      >
        {children}
      </main>
    </div>
  );
}
