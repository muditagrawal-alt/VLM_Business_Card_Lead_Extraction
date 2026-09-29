import { useMutation } from '@tanstack/react-query';
import { motion } from 'motion/react';
import { AlertCircle, ArrowRight, Clock, Cloud, Cpu, Info, ShieldCheck, Table2 } from 'lucide-react';
import { api, ApiError, isAccessCodeError } from '@/api/client';
import { AccessCodePrompt } from '@/components/AccessCodePrompt';
import { UploadZone } from '@/components/UploadZone';
import { useHistory } from '@/hooks/useHistory';
import { useInference } from '@/hooks/useInference';
import { getAccessCode, setAccessCode } from '@/lib/accessCode';
import { riseIn, staggerAt } from '@/lib/motion';
import type { JobCreated } from '@/types/api';

/**
 * The claims worth making, each verifiable in the product itself. The first
 * depends on the deployment, so it is described from the readiness report.
 */
const FIXED_CAPABILITIES = [
  {
    icon: ShieldCheck,
    title: 'Never Invents a Value',
    body: 'Output is constrained to a schema where every field may be null. A card with no job title returns no job title, flagged for review.',
  },
  {
    icon: Table2,
    title: 'Ready for Your CRM',
    body: 'Phone numbers normalised to E.164, emails validated, and the whole batch exported as a formatted workbook.',
  },
] as const;

export function UploadPage({ onCreated }: { onCreated: (jobId: string) => void }) {
  const { entries, remember } = useHistory();
  const inference = useInference();
  const capabilities = [
    { icon: inference.hosted ? Cloud : Cpu, title: inference.title, body: inference.body },
    ...FIXED_CAPABILITIES,
  ];

  const upload = useMutation({
    mutationFn: (files: File[]) => api.createJob(files),
    onSuccess: (created: JobCreated) => {
      remember(created.job_id, created.accepted);
      onCreated(created.job_id);
    },
  });

  return (
    <div className="space-y-10">
      <motion.header variants={riseIn} initial="hidden" animate="visible">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
          Spatial Vision Intelligence
        </p>
        <h1 className="mt-2.5 max-w-3xl text-3xl font-semibold leading-[1.15] tracking-tight sm:text-4xl">
          Capture Anywhere, Extract Instantly.
        </h1>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-muted-foreground">
          Upload a batch of business cards. A vision-language model reads
          each one and returns a structured lead you can review, correct and export —
          without a person retyping a single field.
        </p>
      </motion.header>

      <motion.div variants={riseIn} initial="hidden" animate="visible">
        <UploadZone onSubmit={(files) => upload.mutate(files)} busy={upload.isPending} />
        {inference.disclosure ? (
          <p className="mt-2.5 flex items-start gap-1.5 text-xs leading-relaxed text-muted-foreground">
            <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            {inference.disclosure}
          </p>
        ) : null}
      </motion.div>

      {upload.error && isAccessCodeError(upload.error) ? (
        <AccessCodePrompt
          rejected={getAccessCode() !== null}
          busy={upload.isPending}
          onSubmit={(code) => {
            setAccessCode(code);
            // The mutation still holds the cards that were refused.
            if (upload.variables) upload.mutate(upload.variables);
          }}
        />
      ) : upload.error ? <div
          role="alert"
          aria-live="polite"
          className="flex items-start gap-2.5 rounded-lg border border-destructive/25 bg-destructive/5 p-3.5 text-sm text-destructive"
        >
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div className="min-w-0">
            <p>{upload.error.message}</p>
            {upload.error instanceof ApiError && upload.error.status === 429 && (
              <p className="mt-1 text-xs text-destructive/80">
                Each card costs real inference time on a single server, so uploads are
                rate limited. Try again shortly.
              </p>
            )}
          </div>
        </div> : null}

      {entries.length > 0 && (
        <motion.section variants={riseIn} initial="hidden" animate="visible">
          <h2 className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            <Clock className="size-3.5" aria-hidden="true" /> Your Recent Batches
          </h2>
          <ul className="mt-2.5 divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
            {entries.map((entry) => (
              <li key={entry.jobId}>
                <button
                  type="button"
                  onClick={() => onCreated(entry.jobId)}
                  className="group flex w-full items-center justify-between gap-4 px-4 py-3 text-left text-sm transition-colors duration-150 hover:bg-accent"
                >
                  <span className="tnum">
                    {entry.cards} {entry.cards === 1 ? 'card' : 'cards'}
                  </span>
                  <span className="flex items-center gap-2 text-xs text-muted-foreground">
                    <time dateTime={entry.at}>
                      {new Intl.DateTimeFormat(undefined, {
                        dateStyle: 'medium',
                        timeStyle: 'short',
                      }).format(new Date(entry.at))}
                    </time>
                    <ArrowRight
                      className="size-3.5 transition-transform duration-150 group-hover:translate-x-0.5"
                      aria-hidden="true"
                    />
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-muted-foreground">
            Kept in this browser only. Batches are deleted from the server after seven
            days.
          </p>
        </motion.section>
      )}

      <section aria-label="How it works" className="border-t border-border pt-8">
        <ul className="grid gap-6 sm:grid-cols-3">
          {capabilities.map((item, index) => (
            <motion.li
              key={index}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={staggerAt(index, 0.06, 0.2)}
            >
              <item.icon
                className="size-4 text-muted-foreground"
                aria-hidden="true"
              />
              <h3 className="mt-2.5 text-sm font-medium">{item.title}</h3>
              <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
                {item.body}
              </p>
            </motion.li>
          ))}
        </ul>
      </section>
    </div>
  );
}
