'use client';

import { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { useAppStore } from '@/lib/stores/app-store';
import { cn } from '@/lib/utils';

export function NavigationProgress() {
  const { isNavigating, navigationProgress, completeNavigation } = useAppStore();
  const [mounted, setMounted] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    setMounted(true);
  }, []);

  // Complete navigation whenever the pathname changes
  useEffect(() => {
    if (isNavigating) {
      completeNavigation();
    }
  }, [pathname]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!mounted) return null;

  return (
    <div
      className={cn(
        'fixed top-0 left-0 right-0 z-50 h-[5px] bg-primary',
        'transition-all duration-300 ease-out',
        isNavigating ? 'opacity-100' : 'opacity-0'
      )}
      style={{
        width: `${navigationProgress}%`,
        boxShadow: isNavigating ? '0 0 10px rgba(91, 127, 94, 0.5)' : 'none',
        transition: 'width 0.4s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.3s ease',
        willChange: 'width, opacity',
      }}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={navigationProgress}
      aria-label="Page loading progress"
    />
  );
}
