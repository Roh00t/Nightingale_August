'use client';

import React, { useState, useRef, useEffect } from 'react';
import type { UserRole, RiskLevel } from '@/lib/types';
import { ChevronDown, X, Check } from 'lucide-react';

interface EntryFiltersProps {
  activeRoles: string[];
  activeTypes: string[];
  activeRisks: string[];
  onToggleRole: (role: string) => void;
  onToggleType: (type: string) => void;
  onToggleRisk: (risk: string) => void;
  onClearAll: () => void;
}

const roles: { value: UserRole | 'system'; label: string }[] = [
  { value: 'clinician', label: 'Clinician' },
  { value: 'staff', label: 'Staff' },
  { value: 'patient', label: 'Patient' },
  { value: 'admin', label: 'Admin' },
  { value: 'system', label: 'System' },
];

const entryTypes = [
  { value: 'manual_note', label: 'Manual Note' },
  { value: 'ai_doctor_consult_summary', label: 'AI Doctor Summary' },
  { value: 'ai_nurse_consult_summary', label: 'AI Nurse Summary' },
  { value: 'ai_patient_session_summary', label: 'AI Patient Summary' },
  { value: 'system_event', label: 'Lab Result' },
  { value: 'instruction', label: 'Instruction' },
  { value: 'patient_message', label: 'Patient Message' },
];

const riskLevels: { value: RiskLevel; label: string; color: string }[] = [
  { value: 'critical', label: 'Critical', color: 'bg-red-100 text-red-700' },
  { value: 'high', label: 'High', color: 'bg-orange-100 text-orange-700' },
  { value: 'medium', label: 'Medium', color: 'bg-amber-100 text-amber-700' },
  { value: 'low', label: 'Low', color: 'bg-blue-100 text-blue-700' },
  { value: 'info', label: 'Info', color: 'bg-gray-100 text-gray-700' },
];

interface DropdownProps {
  label: string;
  options: { value: string; label: string; color?: string }[];
  selected: string[];
  onToggle: (value: string) => void;
}

function FilterDropdown({ label, options, selected, onToggle }: DropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const selectedCount = selected.length;

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border transition-colors ${
          selectedCount > 0
            ? 'bg-primary/10 text-primary border-primary/30'
            : 'bg-card text-muted-foreground border-border hover:border-primary/30 hover:text-foreground'
        }`}
      >
        {label}
        {selectedCount > 0 && (
          <span className="w-4 h-4 rounded-full bg-primary text-primary-foreground text-[10px] flex items-center justify-center">
            {selectedCount}
          </span>
        )}
        <ChevronDown className={`w-3.5 h-3.5 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <div className="absolute top-full left-0 mt-1 z-50 min-w-[180px] bg-popover border border-border rounded-lg shadow-lg overflow-hidden">
          <div className="p-1">
            {options.map((option) => {
              const isSelected = selected.includes(option.value);
              return (
                <button
                  key={option.value}
                  onClick={() => onToggle(option.value)}
                  className={`w-full flex items-center gap-2 px-2.5 py-1.5 text-xs rounded-md transition-colors ${
                    isSelected
                      ? 'bg-primary/10 text-primary'
                      : 'hover:bg-secondary text-foreground'
                  }`}
                >
                  <div className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${
                    isSelected ? 'bg-primary border-primary' : 'border-border'
                  }`}>
                    {isSelected && <Check className="w-3 h-3 text-primary-foreground" />}
                  </div>
                  {option.color ? (
                    <span className={`px-1.5 py-0.5 rounded text-[11px] ${option.color}`}>
                      {option.label}
                    </span>
                  ) : (
                    <span>{option.label}</span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export function EntryFilters({
  activeRoles,
  activeTypes,
  activeRisks,
  onToggleRole,
  onToggleType,
  onToggleRisk,
  onClearAll,
}: EntryFiltersProps) {
  const hasFilters = activeRoles.length > 0 || activeTypes.length > 0 || activeRisks.length > 0;
  const totalFilters = activeRoles.length + activeTypes.length + activeRisks.length;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <FilterDropdown
          label="Author"
          options={roles}
          selected={activeRoles}
          onToggle={onToggleRole}
        />
        <FilterDropdown
          label="Severity"
          options={riskLevels}
          selected={activeRisks}
          onToggle={onToggleRisk}
        />
        <FilterDropdown
          label="Type"
          options={entryTypes}
          selected={activeTypes}
          onToggle={onToggleType}
        />

        {hasFilters && (
          <button
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors ml-2"
            onClick={onClearAll}
          >
            <X className="w-3 h-3" />
            Clear {totalFilters}
          </button>
        )}
      </div>

      {/* Active filter pills */}
      {hasFilters && (
        <div className="flex flex-wrap gap-1.5">
          {activeRoles.map((role) => (
            <span
              key={role}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-primary/10 text-primary text-xs"
            >
              {roles.find(r => r.value === role)?.label || role}
              <button onClick={() => onToggleRole(role)} className="hover:bg-primary/20 rounded-full p-0.5">
                <X className="w-2.5 h-2.5" />
              </button>
            </span>
          ))}
          {activeRisks.map((risk) => {
            const riskInfo = riskLevels.find(r => r.value === risk);
            return (
              <span
                key={risk}
                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs ${riskInfo?.color || 'bg-secondary'}`}
              >
                {riskInfo?.label || risk}
                <button onClick={() => onToggleRisk(risk)} className="hover:opacity-70 rounded-full p-0.5">
                  <X className="w-2.5 h-2.5" />
                </button>
              </span>
            );
          })}
          {activeTypes.map((type) => (
            <span
              key={type}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-secondary text-foreground text-xs"
            >
              {entryTypes.find(t => t.value === type)?.label || type}
              <button onClick={() => onToggleType(type)} className="hover:bg-secondary/80 rounded-full p-0.5">
                <X className="w-2.5 h-2.5" />
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
