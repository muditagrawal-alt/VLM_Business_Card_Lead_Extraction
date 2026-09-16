import { AnimatePresence, motion } from 'motion/react';
import { useId, useState } from 'react';
import { FAST } from '@/lib/motion';
import { cn } from '@/lib/utils';

interface Props {
  label: string;
  children: React.ReactNode;
  className?: string;
}

/**
 * A small explanatory popover on hover and on keyboard focus.
 *
 * Implemented as a real button rather than a title attribute: native tooltips
 * cannot be reached by keyboard, take about a second to appear, and cannot
 * hold more than a few words. The content is associated by aria-describedby so
 * a screen reader announces the reason rather than just "warning".
 */
export function InfoTip({ label, children, className }: Props) {
  const [open, setOpen] = useState(false);
  const id = useId();

  return (
    <span className={cn('relative inline-flex', className)}>
      <button
        type="button"
        aria-label={label}
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={(event) => {
          // Tapping must work too: a touch device has no hover.
          event.stopPropagation();
          setOpen((v) => !v);
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setOpen(false);
        }}
        className="inline-flex cursor-help items-center rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        {children}
      </button>

      <AnimatePresence>
        {open ? <motion.span
            id={id}
            role="tooltip"
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={FAST}
            className="absolute left-1/2 top-full z-50 mt-1.5 w-64 -translate-x-1/2 rounded-lg border border-border bg-card p-2.5 text-left text-xs font-normal normal-case leading-relaxed tracking-normal text-foreground shadow-lg"
          >
            {label}
          </motion.span> : null}
      </AnimatePresence>
    </span>
  );
}
