'use client';

import React, { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';

/**
 * Mandatory friction before deferring a high-severity finding.
 *
 * This replaced `window.prompt`, which was the wrong primitive: browsers
 * suppress it after repeated use, sandboxed iframes block it outright, and a
 * suppressed prompt returns null — which read as "no reason given" and silently
 * cancelled the action rather than telling anyone why.
 *
 * The friction is the point. A care team under load clears alerts on autopilot,
 * and a loop that learns from those dismissals learns to hide exactly what it
 * should surface. Requiring a typed reason makes deferring a critical finding a
 * deliberate act, and puts the reason in the audit trail.
 *
 * Low-risk items never reach this dialog — friction on noise is itself a
 * fatigue driver.
 */

export const MIN_REASON_LENGTH = 8;

interface DeferReasonDialogProps {
  open: boolean;
  riskLevel: string;
  /** Shown for context so the reason is written about the right finding. */
  snippet?: string;
  onCancel: () => void;
  onConfirm: (reason: string) => void | Promise<void>;
}

export function DeferReasonDialog({
  open, riskLevel, snippet, onCancel, onConfirm,
}: DeferReasonDialogProps) {
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // Never carry a reason from one finding to the next.
  useEffect(() => {
    if (open) { setReason(''); setSubmitting(false); }
  }, [open]);

  const trimmed = reason.trim();
  const tooShort = trimmed.length < MIN_REASON_LENGTH;

  async function confirm() {
    if (tooShort || submitting) return;
    setSubmitting(true);
    try {
      await onConfirm(trimmed);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onCancel(); }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
            Defer a {riskLevel} finding
          </DialogTitle>
          <DialogDescription className="text-xs">
            A {riskLevel} finding cannot be dismissed without a reason. It is
            recorded in the audit trail and excluded from importance learning,
            so deferring it will not teach the system to hide similar findings.
          </DialogDescription>
        </DialogHeader>

        {snippet && (
          <p className="rounded-md border border-border bg-secondary/40 px-2.5 py-1.5 text-xs text-muted-foreground">
            {snippet}
          </p>
        )}

        <div className="space-y-1.5">
          <label htmlFor="defer-reason" className="text-xs font-medium">
            Why is this safe to defer?
          </label>
          <textarea
            id="defer-reason"
            autoFocus
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') confirm();
            }}
            aria-describedby="defer-reason-help"
            aria-invalid={tooShort}
            placeholder="e.g. Actioned in person during this morning's round"
            className="min-h-[80px] w-full resize-y rounded-lg border border-border bg-secondary/50 p-2.5 text-sm focus:border-primary/40 focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
          <p
            id="defer-reason-help"
            className={`text-[11px] ${tooShort && trimmed.length > 0 ? 'text-red-600' : 'text-muted-foreground'}`}
          >
            {trimmed.length}/{MIN_REASON_LENGTH} characters minimum
          </p>
        </div>

        <DialogFooter className="gap-2 sm:gap-2">
          <Button variant="outline" size="sm" onClick={onCancel} disabled={submitting}>
            Keep it open
          </Button>
          <Button size="sm" onClick={confirm} disabled={tooShort || submitting}>
            {submitting ? 'Recording…' : 'Defer with reason'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
