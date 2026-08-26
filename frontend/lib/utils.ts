import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { formatDistanceToNow, differenceInMonths } from 'date-fns';
import type { RiskLevel, TrustBadge, TrustBadgeType, UserRole } from '@/lib/types';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function getRiskColor(level: RiskLevel): string {
  const colors: Record<RiskLevel, string> = {
    critical: 'text-red-700 bg-red-50 border-red-200',
    high: 'text-amber-700 bg-amber-50 border-amber-200',
    medium: 'text-neutral-600 bg-neutral-50 border-neutral-200',
    low: 'text-neutral-600 bg-neutral-50 border-neutral-200',
    info: 'text-green-600 bg-green-50 border-green-200',
  };
  return colors[level];
}

export function getRiskDotColor(level: RiskLevel): string {
  const colors: Record<RiskLevel, string> = {
    critical: 'bg-red-500',
    high: 'bg-neutral-400',
    medium: 'bg-neutral-400',
    low: 'bg-neutral-400',
    info: 'bg-green-500',
  };
  return colors[level];
}

export function getTrustBadge(authorRole: string, entryType: string): TrustBadge {
  if (entryType.startsWith('ai_')) {
    return { type: 'ai_generated', label: 'AI Generated' };
  }
  const badges: Record<string, TrustBadge> = {
    clinician: { type: 'clinician_verified', label: 'Clinician Verified' },
    patient: { type: 'patient_reported', label: 'Patient Reported' },
    staff: { type: 'staff_noted', label: 'Staff Noted' },
    system: { type: 'ai_generated', label: 'System' },
  };
  return badges[authorRole] || { type: 'staff_noted', label: authorRole };
}

export function getTrustBadgeStyle(type: TrustBadgeType): string {
  const styles: Record<TrustBadgeType, string> = {
    clinician_verified: 'bg-green-100 text-green-700 border-green-300',
    ai_generated: 'bg-blue-100 text-blue-700 border-blue-300',
    patient_reported: 'bg-purple-100 text-purple-700 border-purple-300',
    staff_noted: 'bg-orange-100 text-orange-700 border-orange-300',
    conflict: 'bg-amber-100 text-amber-700 border-amber-300',
  };
  return styles[type];
}

export function getTrustBadgeIcon(type: TrustBadgeType): string {
  const icons: Record<TrustBadgeType, string> = {
    clinician_verified: 'ShieldCheck',
    ai_generated: 'Sparkles',
    patient_reported: 'Heart',
    staff_noted: 'ClipboardList',
    conflict: 'AlertTriangle',
  };
  return icons[type];
}

export function getRoleColor(role: UserRole): string {
  const colors: Record<UserRole, string> = {
    clinician: '#5B7F5E',
    staff: '#737373',
    patient: '#737373',
    admin: '#737373',
  };
  return colors[role];
}

export function getRelativeTime(dateStr: string): string {
  return formatDistanceToNow(new Date(dateStr), { addSuffix: true });
}

export function getFreshnessAge(dateStr: string): 'recent' | 'months' | 'year' | 'old' {
  const months = differenceInMonths(new Date(), new Date(dateStr));
  if (months < 3) return 'recent';
  if (months < 12) return 'months';
  if (months < 24) return 'year';
  return 'old';
}

export function getConfidenceLabel(score: number): { label: string; color: string } {
  if (score >= 0.8) return { label: 'High', color: 'text-green-600' };
  if (score >= 0.5) return { label: 'Medium', color: 'text-neutral-600' };
  return { label: 'Low', color: 'text-red-600' };
}

export function truncate(str: string, length: number): string {
  if (str.length <= length) return str;
  return str.slice(0, length) + '...';
}
