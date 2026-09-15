import { useMutation } from '@tanstack/react-query';
import { AlertCircle, Clock } from 'lucide-react';
import { api, ApiError } from '@/api/client';
import { UploadZone } from '@/components/UploadZone';
import { useHistory } from '@/hooks/useHistory';
import type { JobCreated } from '@/types/api';

export function UploadPage({ onCreated }: { onCreated: (jobId: string) => void }) {
  const { entries, remember } = useHistory();

  const upload = useMutation({
    mutationFn: (files: File[]) => api.createJob(files),
    onSuccess: (created: JobCreated) => {
      remember(created.job_id, created.accepted);
      onCreated(created.job_id);
    },
  });

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-neutral-900">
          Extract leads from business cards
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-neutral-600">
          Upload a batch of cards. Each one is read by a self-hosted Qwen
          vision-language model and turned into a structured lead you can review and
          download as a spreadsheet.
        </p>
      </div>

      <UploadZone onSubmit={(files) => upload.mutate(files)} busy={upload.isPending} />

      {upload.error && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900"
        >
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <div>
            <p>{upload.error.message}</p>
            {upload.error instanceof ApiError && upload.error.status === 429 && (
              <p className="mt-1 text-xs text-red-800">
                Each card costs real inference time on a single server, so uploads are
                rate limited.
              </p>
            )}
          </div>
        </div>
      )}

      {entries.length > 0 && (
        <section>
          <h2 className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-neutral-500">
            <Clock className="size-3.5" aria-hidden /> Your recent batches
          </h2>
          <ul className="mt-2 divide-y divide-neutral-100 overflow-hidden rounded-lg border border-neutral-200 bg-white">
            {entries.map((entry) => (
              <li key={entry.jobId}>
                <button
                  type="button"
                  onClick={() => onCreated(entry.jobId)}
                  className="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm hover:bg-neutral-50"
                >
                  <span className="text-neutral-800">
                    {entry.cards} {entry.cards === 1 ? 'card' : 'cards'}
                  </span>
                  <span className="font-mono text-xs text-neutral-400">
                    {new Date(entry.at).toLocaleString()}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-neutral-400">
            Kept in this browser only. Batches are deleted from the server after seven
            days.
          </p>
        </section>
      )}
    </div>
  );
}
