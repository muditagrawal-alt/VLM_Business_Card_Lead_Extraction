import { cva, type VariantProps } from 'class-variance-authority';
import type { ButtonHTMLAttributes, ReactNode, Ref } from 'react';
import { cn } from '@/lib/utils';

const button = cva(
  [
    'relative inline-flex items-center justify-center gap-2 whitespace-nowrap',
    'rounded-md font-medium',
    // Properties are listed explicitly rather than using `transition-all`,
    // which would animate layout properties and cost a frame.
    'transition-[background-color,border-color,color,opacity,transform] duration-150',
    'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
    'disabled:pointer-events-none disabled:opacity-50',
    'active:scale-[0.98]',
  ].join(' '),
  {
    variants: {
      variant: {
        primary: 'bg-primary text-primary-foreground hover:opacity-90',
        secondary:
          'border border-border bg-card text-foreground hover:bg-accent hover:text-accent-foreground',
        ghost: 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
        destructive:
          'border border-destructive/25 bg-card text-destructive hover:bg-destructive/10',
      },
      size: {
        sm: 'h-8 px-3 text-xs',
        md: 'h-9 px-4 text-sm',
        lg: 'h-11 px-6 text-sm',
        icon: 'size-9',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  },
);

type Props = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof button> & {
    children: ReactNode;
    // React 19 passes ref as an ordinary prop to function components, so no
    // forwardRef wrapper is needed. Declared explicitly because callers need
    // it to move focus — a dialog has to focus its own close button on open.
    ref?: Ref<HTMLButtonElement>;
  };

export function Button({ className, variant, size, children, ref, ...rest }: Props) {
  return (
    <button
      type="button"
      ref={ref}
      className={cn(button({ variant, size }), className)}
      {...rest}
    >
      {children}
    </button>
  );
}
