import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const badgeVariants = cva(
  'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-primary text-primary-foreground',
        secondary: 'border-transparent bg-secondary text-secondary-foreground',
        destructive: 'border-transparent bg-destructive text-destructive-foreground',
        outline: 'text-foreground border-border',
        critical: 'border-red-200/80 bg-red-50 text-red-700',
        high: 'border-neutral-200 bg-neutral-100 text-neutral-700',
        medium: 'border-neutral-200 bg-neutral-100 text-neutral-700',
        low: 'border-neutral-200 bg-neutral-100 text-neutral-700',
        info: 'border-green-200/80 bg-green-50 text-green-700',
        clinician: 'border-green-200/80 bg-green-50 text-green-700',
        ai: 'border-blue-200/80 bg-blue-50 text-blue-700',
        patient: 'border-purple-200/80 bg-purple-50 text-purple-700',
        staff: 'border-orange-200/80 bg-orange-50 text-orange-700',
        conflict: 'border-amber-200/80 bg-amber-50 text-amber-700',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
