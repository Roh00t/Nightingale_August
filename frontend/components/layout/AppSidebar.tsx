'use client';

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Separator } from '@/components/ui/separator';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { getRoleColor } from '@/lib/utils';
import { useAppStore } from '@/lib/stores/app-store';
import type { Profile, UserRole } from '@/lib/types';
import {
  Users,
  LogOut,
  ChevronLeft,
  ChevronRight,
  Activity,
} from 'lucide-react';

interface AppSidebarProps {
  user: Profile | null;
  onLogout: () => void;
}

const navItems = [
  { href: '/patients', label: 'Patients', icon: Users },
];

export function AppSidebar({ user, onLogout }: AppSidebarProps) {
  const pathname = usePathname();
  const { sidebarOpen, toggleSidebar } = useAppStore();
  const collapsed = !sidebarOpen;

  return (
    <aside
      className={`fixed top-0 left-0 h-screen bg-sidebar border-r border-border flex flex-col z-40 transition-sidebar ${
        collapsed ? 'w-[68px]' : 'w-[240px]'
      }`}
    >
      {/* Logo */}
      <div className="flex items-center gap-3 px-4 h-16 shrink-0">
        <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
          <Activity className="w-5 h-5 text-primary" />
        </div>
        {!collapsed && (
          <div className="overflow-hidden">
            <span className="text-foreground font-bold text-base heading-display tracking-tight">
              Nightingale
            </span>
          </div>
        )}
      </div>

      {/* Collapse toggle */}
      <button
        onClick={toggleSidebar}
        className="absolute -right-3 top-[52px] w-6 h-6 rounded-full bg-card border border-border flex items-center justify-center text-muted-foreground hover:text-foreground transition-colors z-50"
      >
        {collapsed ? (
          <ChevronRight className="w-3.5 h-3.5" />
        ) : (
          <ChevronLeft className="w-3.5 h-3.5" />
        )}
      </button>

      <Separator className="bg-border mx-3" />

      {/* Main nav */}
      <nav aria-label="Main navigation" className="flex-1 py-4 px-3 space-y-1 sidebar-scroll overflow-y-auto">
        {navItems.map((item) => {
          const isActive = pathname === item.href || pathname.startsWith(item.href + '/');
          const Icon = item.icon;

          const link = (
            <Link
              key={item.label}
              href={item.href}
              aria-current={isActive ? 'page' : undefined}
              className={`group flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-150 ${
                isActive
                  ? 'bg-primary/10 text-primary'
                  : 'text-muted-foreground hover:bg-sidebar-hover hover:text-foreground'
              }`}
            >
              <Icon className={`w-[18px] h-[18px] shrink-0 ${isActive ? 'text-primary' : ''}`} />
              {!collapsed && <span>{item.label}</span>}
            </Link>
          );

          if (collapsed) {
            return (
              <Tooltip key={item.label}>
                <TooltipTrigger asChild>{link}</TooltipTrigger>
                <TooltipContent side="right" sideOffset={12}>
                  {item.label}
                </TooltipContent>
              </Tooltip>
            );
          }

          return link;
        })}
      </nav>

      <Separator className="bg-border mx-3" />

      {/* Bottom: user + logout */}
      <div className="p-3 space-y-2">
        {/* User profile */}
        {user && (
          <div className="flex items-center gap-3 px-2 py-2">
            <Avatar className="h-8 w-8 shrink-0">
              <AvatarFallback
                style={{
                  backgroundColor: getRoleColor(user.role as UserRole),
                  color: 'white',
                }}
                className="text-xs font-bold"
              >
                {user.display_name
                  .split(' ')
                  .map((n) => n[0])
                  .join('')
                  .slice(0, 2)}
              </AvatarFallback>
            </Avatar>
            {!collapsed && (
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-foreground truncate">
                  {user.display_name}
                </p>
                <p className="text-xs text-muted-foreground capitalize truncate">
                  {user.role}
                </p>
              </div>
            )}
          </div>
        )}

        {/* Logout */}
        {(() => {
          const logoutBtn = (
            <button
              onClick={onLogout}
              className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-muted-foreground hover:bg-red-50 hover:text-red-600 transition-all duration-150"
            >
              <LogOut className="w-4 h-4 shrink-0" />
              {!collapsed && <span>Log Out</span>}
            </button>
          );

          if (collapsed) {
            return (
              <Tooltip>
                <TooltipTrigger asChild>{logoutBtn}</TooltipTrigger>
                <TooltipContent side="right" sideOffset={12}>
                  Log Out
                </TooltipContent>
              </Tooltip>
            );
          }

          return logoutBtn;
        })()}
      </div>
    </aside>
  );
}
