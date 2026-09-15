import { ArrowLeft, Download, FileSpreadsheet, RotateCw } from 'lucide-react';
import { useMemo, useState } from 'react';
import { api } from '@/api/client';
import { LeadDrawer } from '@/components/LeadDrawer';
import { LeadsTable } from '@/components/LeadsTable';
import { Button } from '@/components/ui/Button';
import { Spinner } from '@/components/ui/Spinner';
import { useJob, useLeads, useRetryTask, useUpdateLead } from '@/hooks/useJob';
import { isJobSettled, type Lead, type Task } from '@/types/api';

function formatEta(seconds: number | null): string {
  if (seconds === null) return 'estimating…';
  if (seconds <= 0) return 'finishing up';
  if (seconds < 60) return `about ${seconds}s remaining`;
  return `about ${Math.ceil(seconds / 60)} min remaining`;
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

  const failed = (job?.tasks ?? []).filter((task) => task.status === 'failed');

  if (isLoading) {
    return (
      <p className="flex items-center gap-2 py-16 text-sm text-neutral-500">
        <Spinner /> Loading this batch…
      </p>
    );
  }

  if (error || !job) {
    return (
      <div className="py-16 text-center">
        <p className="text-sm text-neutral-700">
          {error instanceof Error ? error.message : 'This batch could not be found.'}
        </p>
        <Button variant="secondary" size="sm" className="mt-4" onClick={onBack}>
          Upload some cards
        </Button>
      </div>
    );
  }

  const settled = isJobSettled(job);
  const processed = job.done + job.failed;
  const percent = job.total > 0 ? Math.round((processed / job.total) * 100) : 0;
  const hasLeads = (leads?.length ?? 0) > 0;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="size-4" aria-hidden /> New batch
        </Button>

        <div className="flex gap-2">
          <a
            href={api.exportUrl(jobId, 'csv')}
            className={
              hasLeads
                ? 'inline-flex h-9 items-center gap-2 rounded-md border border-neutral-300 bg-white px-4 text-sm font-medium text-neutral-800 hover:bg-neutral-50'
                : 'pointer-events-none inline-flex h-9 items-center gap-2 rounded-md border border-neutral-200 px-4 text-sm text-neutral-400'
            }
            aria-disabled={!hasLeads}
          >
            <Download className="size-4" aria-hidden /> CSV
          </a>
          <a
            href={api.exportUrl(jobId, 'xlsx')}
            className={
              hasLeads
                ? 'inline-flex h-9 items-center gap-2 rounded-md bg-accent-600 px-4 text-sm font-medium text-white hover:bg-accent-700'
                : 'pointer-events-none inline-flex h-9 items-center gap-2 rounded-md bg-neutral-200 px-4 text-sm text-neutral-400'
            }
            aria-disabled={!hasLeads}
          >
            <FileSpreadsheet className="size-4" aria-hidden /> Download Excel
          </a>
        </div>
      </div>

      <section
        aria-label="Batch progress"
        className="rounded-lg border border-neutral-200 bg-white p-4"
      >
        <div className="flex items-baseline justify-between gap-4">
          <p className="text-sm text-neutral-800">
            <span className="font-semibold">{processed}</span> of {job.total} cards processed
            {job.failed > 0 && (
              <span className="text-red-700"> · {job.failed} failed</span>
            )}
          </p>
          <p className="text-xs text-neutral-500" aria-live="polite">
            {settled ? 'Complete' : formatEta(job.estimated_seconds_remaining)}
          </p>
        </div>

        <div
          role="progressbar"
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Cards processed"
          className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-neutral-100"
        >
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              job.status === 'failed' ? 'bg-red-500' : 'bg-accent-600'
            }`}
            style={{ width: `${percent}%` }}
          />
        </div>

        {!settled && job.queue_depth > job.pending && (
          <p className="mt-2 text-xs text-neutral-500">
            {job.queue_depth} cards are queued in total, so this batch may wait behind
            another.
          </p>
        )}
      </section>

      {failed.length > 0 && (
        <section className="rounded-lg border border-red-200 bg-red-50 p-4">
          <h2 className="text-sm font-medium text-red-900">
            {failed.length} {failed.length === 1 ? 'card' : 'cards'} could not be read
          </h2>
          <ul className="mt-2 space-y-1.5">
            {failed.map((task) => (
              <li key={task.id} className="flex items-center justify-between gap-3 text-xs">
                <span className="text-red-800">
                  <span className="font-medium">{task.original_filename ?? 'card'}</span>
                  {task.error && <span className="text-red-700"> — {task.error}</span>}
                </span>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => retryTask.mutate(task.id)}
                  disabled={retryTask.isPending}
                >
                  <RotateCw className="size-3" aria-hidden /> Retry
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {hasLeads ? (
        <LeadsTable
          leads={leads ?? []}
          tasksByImage={tasksByImage}
          onSelect={setSelected}
        />
      ) : (
        <p className="rounded-lg border border-dashed border-neutral-300 bg-white py-12 text-center text-sm text-neutral-500">
          {settled
            ? 'No leads were extracted from this batch.'
            : 'Leads will appear here as each card finishes.'}
        </p>
      )}

      {selected && (
        <LeadDrawer
          lead={selected}
          saving={updateLead.isPending}
          onClose={() => setSelected(null)}
          onSave={(changes) =>
            updateLead.mutate(
              { leadId: selected.id, changes },
              { onSuccess: (updated) => setSelected(updated) },
            )
          }
        />
      )}
    </div>
  );
}
