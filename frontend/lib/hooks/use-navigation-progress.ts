'use client';

import { useCallback, useRef } from 'react';
import { useAppStore } from '@/lib/stores/app-store';

export function useNavigationProgress() {
  const { startNavigation, setProgress } = useAppStore();
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  const clearProgressInterval = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  const startWithProgress = useCallback(() => {
    clearProgressInterval();
    startNavigation();
    setProgress(30);

    let progress = 30;
    intervalRef.current = setInterval(() => {
      progress += 3;
      if (progress >= 90) {
        clearProgressInterval();
        setProgress(90);
        return;
      }
      setProgress(progress);
    }, 40);
  }, [startNavigation, setProgress, clearProgressInterval]);

  return { startNavigation: startWithProgress };
}
