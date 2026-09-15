import { AnimatePresence, motion } from 'motion/react';
import { ArrowLeft, Download, FileSpreadsheet, RotateCw } from 'lucide-react';
import { useMemo, useState } from 'react';
import { api } from '@/api/client';
import { LeadDrawer } from '@/components/LeadDrawer';
import { LeadsTable } from '@/components/LeadsTable';
import { ProgressPanel } from '@/components/ProgressPanel';
import { Button } from '@/components/ui/Button';
import { Spinner } from '@/components/ui/Spinner';
import { useJob, useLeads, useRetryTask, useUpdateLead } from '@/hooks/useJob';
import { FAST, riseIn } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { isJobSettled, type Lead, type Task } from '@/types/api';

/** Export links are real anchors so the browser performs the download. */
function ExportLink({
  href,
  enabled,
  primary,
  children,
}: {
  href: string;
  enabled: boolean;
  primary?: boolean;
  children: React.ReactNode;
}) {
  const base =
    'inline-flex h-9 items-center gap-2 rounded-md px-4 text-sm font-medium transition-[background-color,opacity] duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring';
  if (!enabled) {
    return (
      <span
        aria-disabled="true"
        title="Available once at least one card has been extracted"
        className={cn(base, 'cursor-not-allowed border border-border text-muted-foreground/50')}
      >
        {children}
      </span>
    );
  }
  return (
    <a
      href={href}
      className={cn(
        base,
        primary
          ? 'bg-primary text-primary-foreground hover:opacity-90'
          : 'border border-border bg-card hover:bg-accent',
      )}
    >
      {children}
    </a>
  );
}

export function JobPage({ jobId, onBack }: { jobId: string; onBack: () => void }) {
  const { data: job, isLoading, error } = useJob(jobId);
  const { data: leads } = useLeads(jobId, job);
  const updateLead = useUpdateLead(jobId);
  const retryTask = useRetryTask(jobId);
  const [selected, setSelected] = useState<Lead | null>(null);

  const tasksByImage = useMemo(() => {
    const map = new Map<string, Task>();
    for (const task of job?.tasks ?? []) map.set(task.image_id, task);
    return map;
  }, [job?.tasks]);

  const failed = useMemo(
    () => (job?.tasks ?? []).filter((task) => task.status === 'failed'),
    [job?.tasks],
  );

  if (isLoading) {
    return (
      <p className="flex items-center gap-2 py-20 text-sm text-muted-foreground">
        <Spinner /> Loading this batch…
      </p>
    );
  }

  if (error || !job) {
    return (
      <div className="py-20 text-center">
        <p className="text-sm">
          {error instanceof Error ? error.message : 'This batch could not be found.'}
        </p>
        <Button variant="secondary" size="sm" className="mt-4" onClick={onBack}>
          Upload Some Cards
        </Button>
      </div>
    );
  }

  const settled = isJobSettled(job);
  const hasLeads = (leads?.length ?? 0) > 0;

  return (
    <motion.div variants={riseIn} initial="hidden" animate="visible" className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="size-4" aria-hidden="true" /> New Batch
        </Button>

        <div className="flex gap-2">
          <ExportLink href={api.exportUrl(jobId, 'csv')} enabled={hasLeads}>
            <Download className="size-4" aria-hidden="true" /> CSV
          </ExportLink>
          <ExportLink href={api.exportUrl(jobId, 'xlsx')} enabled={hasLeads} primary>
            <FileSpreadsheet className="size-4" aria-hidden="true" /> Download Excel
          </ExportLink>
        </div>
      </div>

      <ProgressPanel job={job} />

      <AnimatePresence>
        {failed.length > 0 && (
          <motion.section
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={FAST}
            className="overflow-hidden rounded-xl border border-destructive/25 bg-destructive/5 p-4"
          >
            <h2 className="text-sm font-medium text-destructive">
              <span className="tnum">{failed.length}</span>{' '}
              {failed.length === 1 ? 'card' : 'cards'} could not be read
            </h2>
            <ul className="mt-2.5 space-y-2">
              {failed.map((task) => (
                <li
                  key={task.id}
                  className="flex flex-wrap items-center justify-between gap-3 text-xs"
                >
                  <span className="min-w-0 text-destructive">
                    <span className="font-medium">
                      {task.original_filename ?? 'card'}
                    </span>
                    {task.error ? <span className="text-destructive/80"> — {task.error}</span> : null}
                  </span>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => retryTask.mutate(task.id)}
                    disabled={retryTask.isPending}
                  >
                    {retryTask.isPending ? (
                      <Spinner className="size-3" />
                    ) : (
                      <RotateCw className="size-3" aria-hidden="true" />
                    )}
                    Retry
                  </Button>
                </li>
              ))}
            </ul>
          </motion.section>
        )}
      </AnimatePresence>

      {hasLeads ? (
        <LeadsTable leads={leads ?? []} tasksByImage={tasksByImage} onSelect={setSelected} />
      ) : (
        <p className="rounded-xl border border-dashed border-border bg-card py-16 text-center text-sm text-muted-foreground">
          {settled
            ? 'No leads were extracted from this batch.'
            : 'Leads appear here as each card finishes.'}
        </p>
      )}

      {selected ? <LeadDrawer
          lead={selected}
          saving={updateLead.isPending}
          onClose={() => setSelected(null)}
          onSave={(changes) =>
            updateLead.mutate(
              { leadId: selected.id, changes },
              { onSuccess: (updated) => setSelected(updated) },
            )
          }
        /> : null}
    </motion.div>
  );
}
