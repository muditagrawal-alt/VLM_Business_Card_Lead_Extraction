import { cn } from '@/lib/utils';

/**
 * aria-hidden because the surrounding text carries the status. A spinner
 * announced on its own tells a screen reader user nothing.
 */
export function Spinner({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'inline-block size-4 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent',
        className,
      )}
    />
  );
}
