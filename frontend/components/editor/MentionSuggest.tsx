'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { getRoleColor } from '@/lib/utils';
import type { Profile, UserRole } from '@/lib/types';

interface MentionSuggestProps {
  query: string;
  members: Profile[];
  onSelect: (member: Profile) => void;
  position: { top: number; left: number } | null;
}

export function MentionSuggest({ query, members, onSelect, position }: MentionSuggestProps) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  const filtered = members.filter((m) =>
    m.display_name.toLowerCase().includes(query.toLowerCase())
  );

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIndex((i) => Math.max(i - 1, 0));
      } else if (e.key === 'Enter' && filtered[selectedIndex]) {
        e.preventDefault();
        onSelect(filtered[selectedIndex]);
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [filtered, selectedIndex, onSelect]);

  if (!position || filtered.length === 0) return null;

  return (
    <div
      ref={listRef}
      className="absolute z-50 bg-popover border rounded-lg shadow-lg overflow-hidden"
      style={{ top: position.top, left: position.left }}
    >
      {filtered.map((member, idx) => (
        <button
          key={member.id}
          className={`flex items-center gap-2 w-full px-3 py-1.5 text-sm text-left transition-colors ${
            idx === selectedIndex ? 'bg-accent' : 'hover:bg-accent/50'
          }`}
          onClick={() => onSelect(member)}
        >
          <Avatar className="h-5 w-5">
            <AvatarFallback
              style={{ backgroundColor: getRoleColor(member.role as UserRole), color: 'white' }}
              className="text-[9px] font-bold"
            >
              {member.display_name.split(' ').map((n) => n[0]).join('').slice(0, 2)}
            </AvatarFallback>
          </Avatar>
          <span className="font-medium">{member.display_name}</span>
          <span className="text-xs text-muted-foreground ml-auto capitalize">{member.role}</span>
        </button>
      ))}
    </div>
  );
}
