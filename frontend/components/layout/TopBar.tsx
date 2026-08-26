'use client';

import React from 'react';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { getRoleColor } from '@/lib/utils';
import { useAppStore } from '@/lib/stores/app-store';
import { LogOut } from 'lucide-react';
import type { Profile, UserRole } from '@/lib/types';

interface TopBarProps {
  user: Profile | null;
  title?: string;
  subtitle?: string;
}

export function TopBar({ user, title, subtitle }: TopBarProps) {
  const { currentUser, logoutHandler } = useAppStore();
  const activeRole = currentUser?.role || user?.role;

  return (
    <header className="h-16 border-b border-border bg-card flex items-center justify-between px-4 sm:px-8 sticky top-0 z-30">
      {/* Page title */}
      <div className="min-w-0 flex-1">
        {title && (
          <h1 className="text-lg sm:text-xl font-semibold heading-display text-foreground truncate">
            {title}
          </h1>
        )}
        {subtitle && (
          <p className="text-sm text-muted-foreground truncate">{subtitle}</p>
        )}
      </div>

      {/* Right side actions */}
      <div className="flex items-center gap-2 sm:gap-3 shrink-0">
        {/* User chip */}
        {user && (
          <div className="flex items-center gap-2.5 pl-1">
            <div className="text-right hidden sm:block">
              <p className="text-sm font-medium leading-tight">{user.display_name}</p>
              <p className="text-xs text-muted-foreground capitalize">{activeRole}</p>
            </div>
            <Avatar className="h-9 w-9">
              <AvatarFallback
                style={{
                  backgroundColor: getRoleColor((activeRole || 'clinician') as UserRole),
                  color: 'white',
                }}
                className="text-xs font-bold"
              >
                {user.display_name.split(' ').map((n) => n[0]).join('').slice(0, 2)}
              </AvatarFallback>
            </Avatar>
          </div>
        )}
        {/* Logout button */}
        {logoutHandler && (
          <button
            onClick={logoutHandler}
            className="p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
            title="Sign out"
          >
            <LogOut className="w-4 h-4" />
          </button>
        )}
      </div>
    </header>
  );
}
