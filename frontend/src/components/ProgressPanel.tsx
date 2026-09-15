import { motion, useReducedMotion } from 'motion/react';
import { Spinner } from '@/components/ui/Spinner';
import { NORMAL } from '@/lib/motion';
import { isJobSettled, type Job } from '@/types/api';

/**
 * Formats the backend's estimate.
 *
 * Null is rendered as "estimating…" rather than a number: the inference tiers
 * differ by an order of magnitude, so a figure invented before the first card
 * finishes could be wrong by minutes.
 */
function formatEta(seconds: number | null): string {
  if (seconds === null) return 'Estimating…';
  if (seconds <= 0) return 'Finishing up…';
  if (seconds < 60) return `About ${seconds}s remaining`;
  return `About ${Math.ceil(seconds / 60)} min remaining`;
}

export function ProgressPanel({ job }: { job: Job }) {
  const reduced = useReducedMotion();
  const settled = isJobSettled(job);
  const processed = job.done + job.failed;
  const percent = job.total > 0 ? Math.round((processed / job.total) * 100) : 0;

  return (
    <section
      aria-label="Batch progress"
      className="rounded-xl border border-border bg-card p-4"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm">
          <span className="tnum font-semibold">{processed}</span>
          <span className="text-muted-foreground"> of </span>
          <span className="tnum font-semibold">{job.total}</span>
          <span className="text-muted-foreground">
            {' '}
            {job.total === 1 ? 'card' : 'cards'} processed
          </span>
          {job.failed > 0 && (
            <span className="tnum text-destructive"> · {job.failed} failed</span>
          )}
        </p>

        <p
          aria-live="polite"
          className="flex items-center gap-1.5 text-xs text-muted-foreground"
        >
          {!settled && <Spinner className="size-3" />}
          {settled ? 'Complete' : formatEta(job.estimated_seconds_remaining)}
        </p>
      </div>

      <div
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Cards processed"
        className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-muted"
      >
        <motion.div
          className={
            job.status === 'failed' ? 'h-full bg-destructive' : 'h-full bg-primary'
          }
          initial={false}
          animate={{ width: `${percent}%` }}
          transition={reduced ? { duration: 0 } : NORMAL}
        />
      </div>

      {!settled && job.queue_depth > job.pending && (
        <p className="mt-2 text-xs text-muted-foreground">
          <span className="tnum">{job.queue_depth}</span> cards are queued in total, so
          this batch may be waiting behind another.
        </p>
      )}
    </section>
  );
}
