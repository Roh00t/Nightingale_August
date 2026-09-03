'use client';

import { Loader2, MessageSquare, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';

interface AIActionsCardProps {
  /**
   * Whether the caller may generate a summary — i.e. `activeRole === 'clinician'`.
   *
   * A capability boolean, not a role. Every role comparison stays in
   * PatientWorkspace so a reviewer can audit who-sees-what by reading one file,
   * and so this component cannot quietly acquire a second opinion about
   * authorisation.
   */
  canGenerateSummary: boolean;
  onGenerateSummary: () => void;
  summarising: boolean;
  onDraftMessage: () => void;
  drafting: boolean;
}

export function AIActionsCard({
  canGenerateSummary,
  onGenerateSummary,
  summarising,
  onDraftMessage,
  drafting,
}: AIActionsCardProps) {
  return (
    <Card className="self-start">
      <CardContent className="pt-4 pb-3 space-y-2">
        <h3 className="text-sm font-semibold mb-3">AI Actions</h3>
        {canGenerateSummary && (
          <Button
            size="sm"
            className="w-full text-xs gap-2"
            onClick={onGenerateSummary}
            disabled={summarising}
          >
            {summarising ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Sparkles className="w-3.5 h-3.5" />
            )}
            {summarising ? 'Generating...' : 'Generate AI Summary'}
          </Button>
        )}
        <Button
          size="sm"
          variant="outline"
          className="w-full text-xs gap-2"
          onClick={onDraftMessage}
          disabled={drafting}
        >
          {drafting ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <MessageSquare className="w-3.5 h-3.5" />
          )}
          {drafting ? 'Drafting...' : 'Message Patient'}
        </Button>
      </CardContent>
    </Card>
  );
}
