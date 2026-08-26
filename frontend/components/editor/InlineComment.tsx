'use client';

import React, { useState, useRef, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Badge } from '@/components/ui/badge';
import { MentionSuggest } from './MentionSuggest';
import { getRoleColor, getRelativeTime } from '@/lib/utils';
import { Loader2 } from 'lucide-react';
import type { Comment, UserRole, Profile } from '@/lib/types';

interface InlineCommentProps {
  comments: Comment[];
  currentUser: Profile;
  entryId: string;
  onSubmit: (content: string, parentId?: string, mentions?: string[]) => void;
  onResolve: (commentId: string) => void;
  loading?: boolean;
  clinicMembers?: Profile[];
}

export function InlineComment({
  comments,
  currentUser,
  entryId,
  onSubmit,
  onResolve,
  loading,
  clinicMembers = [],
}: InlineCommentProps) {
  const [newComment, setNewComment] = useState('');
  const [replyTo, setReplyTo] = useState<string | null>(null);
  const [mentionQuery, setMentionQuery] = useState('');
  const [mentionPosition, setMentionPosition] = useState<{ top: number; left: number } | null>(null);
  const [mentionedUsers, setMentionedUsers] = useState<Profile[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setNewComment(value);

    // Detect @ mention trigger
    const cursorPos = e.target.selectionStart || 0;
    const textBeforeCursor = value.slice(0, cursorPos);
    const mentionMatch = textBeforeCursor.match(/@(\w*)$/);

    if (mentionMatch) {
      setMentionQuery(mentionMatch[1]);
      // Calculate position for dropdown
      if (inputRef.current) {
        const rect = inputRef.current.getBoundingClientRect();
        setMentionPosition({
          top: rect.height + 4,
          left: 0,
        });
      }
    } else {
      setMentionQuery('');
      setMentionPosition(null);
    }
  }, []);

  const handleMentionSelect = useCallback((member: Profile) => {
    const cursorPos = inputRef.current?.selectionStart || 0;
    const textBeforeCursor = newComment.slice(0, cursorPos);
    const textAfterCursor = newComment.slice(cursorPos);

    // Replace @query with @DisplayName
    const newText = textBeforeCursor.replace(/@\w*$/, `@${member.display_name} `) + textAfterCursor;
    setNewComment(newText);

    // Track mentioned user
    if (!mentionedUsers.find(u => u.id === member.id)) {
      setMentionedUsers([...mentionedUsers, member]);
    }

    setMentionQuery('');
    setMentionPosition(null);
    inputRef.current?.focus();
  }, [newComment, mentionedUsers]);

  const handleSubmit = () => {
    if (!newComment.trim()) return;
    const mentionIds = mentionedUsers.map(u => u.id);
    onSubmit(newComment.trim(), replyTo || undefined, mentionIds.length > 0 ? mentionIds : undefined);
    setNewComment('');
    setReplyTo(null);
    setMentionedUsers([]);
  };

  // Organize into threads
  const rootComments = comments.filter((c) => !c.parent_comment_id);
  const repliesByParent = new Map<string, Comment[]>();
  comments
    .filter((c) => c.parent_comment_id)
    .forEach((c) => {
      const existing = repliesByParent.get(c.parent_comment_id!) || [];
      existing.push(c);
      repliesByParent.set(c.parent_comment_id!, existing);
    });

  return (
    <div className="space-y-3">
      {rootComments.map((comment) => (
        <CommentThread
          key={comment.id}
          comment={comment}
          replies={repliesByParent.get(comment.id) || []}
          onReply={() => setReplyTo(comment.id)}
          onResolve={() => onResolve(comment.id)}
          canResolve={currentUser.role === 'clinician' || currentUser.role === 'admin'}
          currentUserId={currentUser.id}
        />
      ))}

      {/* New comment input */}
      <div className="flex gap-2 items-start">
        <Avatar className="h-6 w-6 shrink-0">
          <AvatarFallback
            style={{ backgroundColor: getRoleColor(currentUser.role as UserRole), color: 'white' }}
            className="text-xs font-bold"
          >
            {currentUser.display_name.split(' ').map((n) => n[0]).join('').slice(0, 2)}
          </AvatarFallback>
        </Avatar>
        <div className="flex-1">
          {replyTo && (
            <div className="flex items-center gap-1 mb-1">
              <span className="text-xs text-muted-foreground">Replying to thread</span>
              <Button
                size="sm"
                variant="ghost"
                className="h-4 text-xs px-1"
                onClick={() => setReplyTo(null)}
              >
                Cancel
              </Button>
            </div>
          )}
          <div className="flex gap-1 relative">
            <input
              ref={inputRef}
              type="text"
              value={newComment}
              onChange={handleInputChange}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !mentionPosition) handleSubmit();
                if (e.key === 'Escape') {
                  setMentionPosition(null);
                  setMentionQuery('');
                }
              }}
              placeholder="Add a comment... (use @ to mention)"
              className="flex-1 text-xs border rounded-md px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary"
            />
            <Button
              size="sm"
              className="h-7 text-xs px-2"
              onClick={handleSubmit}
              disabled={!newComment.trim() || loading}
            >
              {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : 'Send'}
            </Button>
            {/* Mention suggestions dropdown */}
            <MentionSuggest
              query={mentionQuery}
              members={clinicMembers.filter(m => m.id !== currentUser.id)}
              onSelect={handleMentionSelect}
              position={mentionPosition}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

// Helper to render comment content with highlighted @mentions
// Captures names with up to 3 words (e.g., "Dr. Sarah Chen", "Nurse James Rivera")
function renderContentWithMentions(content: string): React.ReactNode {
  const mentionRegex = /@([\w.]+(?:\s+[\w.]+){0,2})/g;
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  let match;

  while ((match = mentionRegex.exec(content)) !== null) {
    // Add text before mention
    if (match.index > lastIndex) {
      parts.push(content.slice(lastIndex, match.index));
    }
    // Add highlighted mention
    parts.push(
      <span key={match.index} className="text-primary font-medium">
        @{match[1]}
      </span>
    );
    lastIndex = match.index + match[0].length;
  }

  // Add remaining text
  if (lastIndex < content.length) {
    parts.push(content.slice(lastIndex));
  }

  return parts.length > 0 ? parts : content;
}

function CommentThread({
  comment,
  replies,
  onReply,
  onResolve,
  canResolve,
  currentUserId,
}: {
  comment: Comment;
  replies: Comment[];
  onReply: () => void;
  onResolve: () => void;
  canResolve: boolean;
  currentUserId: string;
}) {
  const roleColor = getRoleColor(comment.author_role as UserRole);
  const isMentioned = comment.mentions?.includes(currentUserId);

  return (
    <div className={`text-xs ${comment.is_resolved ? 'opacity-50' : ''}`}>
      <div className={`flex items-start gap-2 p-2 -m-2 rounded-lg transition-colors ${
        isMentioned ? 'bg-primary/10 ring-1 ring-primary/20' : ''
      }`}>
        <Avatar className="h-5 w-5 shrink-0 mt-0.5">
          <AvatarFallback
            style={{ backgroundColor: roleColor, color: 'white' }}
            className="text-[11px] font-bold"
          >
            {comment.author?.display_name?.split(' ').map((n) => n[0]).join('').slice(0, 2) || '??'}
          </AvatarFallback>
        </Avatar>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="font-medium" style={{ color: roleColor }}>
              {comment.author?.display_name || comment.author_role}
            </span>
            <span className="text-muted-foreground">{getRelativeTime(comment.created_at)}</span>
            {comment.is_resolved && (
              <Badge variant="outline" className="text-[11px] px-1 py-0">Resolved</Badge>
            )}
          </div>
          <p className="mt-0.5 text-foreground">{renderContentWithMentions(comment.content)}</p>
          <div className="flex items-center gap-2 mt-1">
            <button onClick={onReply} className="text-primary hover:underline text-xs">
              Reply
            </button>
            {canResolve && !comment.is_resolved && (
              <button onClick={onResolve} className="text-green-600 hover:underline text-xs">
                Resolve
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Replies */}
      {replies.length > 0 && (
        <div className="ml-7 mt-1.5 space-y-1.5 border-l-2 border-muted pl-2">
          {replies.map((reply) => {
            const isReplyMentioned = reply.mentions?.includes(currentUserId);
            return (
              <div
                key={reply.id}
                className={`flex items-start gap-1.5 p-1.5 -m-1.5 rounded-md transition-colors ${
                  isReplyMentioned ? 'bg-primary/10 ring-1 ring-primary/20' : ''
                }`}
              >
                <Avatar className="h-4 w-4 shrink-0 mt-0.5">
                  <AvatarFallback
                    style={{ backgroundColor: getRoleColor(reply.author_role as UserRole), color: 'white' }}
                    className="text-[10px] font-bold"
                  >
                    {reply.author?.display_name?.split(' ').map((n) => n[0]).join('').slice(0, 2) || '??'}
                  </AvatarFallback>
                </Avatar>
                <div>
                  <span className="font-medium" style={{ color: getRoleColor(reply.author_role as UserRole) }}>
                    {reply.author?.display_name || reply.author_role}
                  </span>
                  <span className="text-muted-foreground ml-1">{renderContentWithMentions(reply.content)}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
