import { cva, type VariantProps } from 'class-variance-authority';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

const badge = cva(
  'inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider',
  {
    variants: {
      tone: {
        neutral: 'bg-muted text-muted-foreground',
        warning: 'bg-warning-surface text-warning-foreground',
        destructive: 'bg-destructive/10 text-destructive',
        outline: 'border border-border text-muted-foreground',
      },
    },
    defaultVariants: { tone: 'neutral' },
  },
);

type Props = VariantProps<typeof badge> & {
  children: ReactNode;
  className?: string;
  title?: string;
};

export function Badge({ tone, children, className, title }: Props) {
  return (
    <span title={title} className={cn(badge({ tone }), className)}>
      {children}
    </span>
  );
}
