import { create } from 'zustand';
import type { Profile, UserRole } from '@/lib/types';

interface AppState {
  // Current user profile
  currentUser: Profile | null;
  setCurrentUser: (user: Profile | null) => void;

  // Logout handler (set by layout)
  logoutHandler: (() => void) | null;
  setLogoutHandler: (handler: (() => void) | null) => void;

  // Active patient context
  activePatientId: string | null;
  setActivePatientId: (id: string | null) => void;

  // UI state
  sidebarOpen: boolean;
  toggleSidebar: () => void;
  commandPaletteOpen: boolean;
  setCommandPaletteOpen: (open: boolean) => void;

  // Timeline filters
  timelineFilters: {
    authorRoles: UserRole[];
    entryTypes: string[];
    riskLevels: string[];
    dateRange: { from: string | null; to: string | null };
  };
  setTimelineFilters: (filters: Partial<AppState['timelineFilters']>) => void;

  // Provenance navigation
  highlightedEntryId: string | null;
  setHighlightedEntryId: (id: string | null) => void;

  // Navigation progress
  isNavigating: boolean;
  navigationProgress: number;
  startNavigation: () => void;
  completeNavigation: () => void;
  setProgress: (progress: number) => void;
}

export const useAppStore = create<AppState>((set) => ({
  currentUser: null,
  setCurrentUser: (user) => set({ currentUser: user }),

  logoutHandler: null,
  setLogoutHandler: (handler) => set({ logoutHandler: handler }),

  activePatientId: null,
  setActivePatientId: (id) => set({ activePatientId: id }),

  sidebarOpen: true,
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  commandPaletteOpen: false,
  setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),

  timelineFilters: {
    authorRoles: [],
    entryTypes: [],
    riskLevels: [],
    dateRange: { from: null, to: null },
  },
  setTimelineFilters: (filters) =>
    set((s) => ({
      timelineFilters: { ...s.timelineFilters, ...filters },
    })),

  highlightedEntryId: null,
  setHighlightedEntryId: (id) => set({ highlightedEntryId: id }),

  // Navigation progress
  isNavigating: false,
  navigationProgress: 0,

  startNavigation: () => set({ isNavigating: true, navigationProgress: 0 }),

  setProgress: (progress: number) => set({ navigationProgress: Math.min(progress, 100) }),

  completeNavigation: () => {
    set({ navigationProgress: 100 });
    setTimeout(() => {
      set({ isNavigating: false, navigationProgress: 0 });
    }, 300);
  },
}));
