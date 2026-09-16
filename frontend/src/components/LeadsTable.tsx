import { motion } from 'motion/react';
import { AlertTriangle, Check, Cloud, Copy, Cpu, Server } from 'lucide-react';
import { useState } from 'react';
import { api } from '@/api/client';
import { Badge } from '@/components/ui/Badge';
import { InfoTip } from '@/components/ui/InfoTip';
import { explainConfidence, LOW_CONFIDENCE } from '@/lib/confidence';
import { staggerAt } from '@/lib/motion';
import { cn } from '@/lib/utils';
import {
  LEAD_FIELDS,
  type Lead,
  type LeadField,
  type ProviderTier,
  type Task,
} from '@/types/api';

const HEADINGS: Record<LeadField, string> = {
  first_name: 'First Name',
  last_name: 'Last Name',
  position: 'Position',
  company: 'Company',
  location: 'Location',
  phone: 'Phone',
  email: 'Email',
};

const TIER_ICON: Record<ProviderTier, typeof Cpu> = {
  gpu: Server,
  cpu: Cpu,
  cloud: Cloud,
};

const TIER_TITLE: Record<ProviderTier, string> = {
  gpu: 'Read by the self-hosted model on GPU',
  cpu: 'Read by the self-hosted fallback model on CPU',
  cloud: 'Read by the hosted Qwen API — this card left our server',
};

export function TierBadge({ task }: { task: Task | undefined }) {
  if (!task?.provider) return null;
  const Icon = TIER_ICON[task.provider];
  const latency = task.latency_ms ? ` · ${(task.latency_ms / 1000).toFixed(1)}s` : '';
  return (
    <Badge
      tone={task.provider === 'cloud' ? 'warning' : 'neutral'}
      title={`${TIER_TITLE[task.provider]}${task.model ? ` (${task.model})` : ''}${latency}`}
    >
      <Icon className="size-3" aria-hidden="true" />
      {task.provider}
    </Badge>
  );
}

interface Props {
  leads: Lead[];
  tasksByImage: Map<string, Task>;
  onSelect: (lead: Lead) => void;
}

export function LeadsTable({ leads, tasksByImage, onSelect }: Props) {
  const [copied, setCopied] = useState<string | null>(null);

  const copy = async (value: string, key: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(key);
      window.setTimeout(() => setCopied(null), 1200);
    } catch {
      // Clipboard access can be denied; the text remains selectable.
    }
  };

  return (
    // Wide content scrolls inside its own container so the page itself never
    // scrolls sideways on a phone.
    <div className="overflow-x-auto rounded-xl border border-border bg-card">
      <table className="w-full min-w-[1020px] border-collapse text-sm">
        <caption className="sr-only">
          Leads extracted from the uploaded business cards. Select a row to see the
          card and edit its fields.
        </caption>
        <thead>
          <tr className="border-b border-border bg-muted/40 text-left">
            <th
              scope="col"
              className="w-16 whitespace-nowrap px-3 py-2.5 text-xs font-medium text-muted-foreground"
            >
              Card
            </th>
            {LEAD_FIELDS.map((field) => (
              <th
                key={field}
                scope="col"
                className="whitespace-nowrap px-3 py-2.5 text-xs font-medium text-muted-foreground"
              >
                {HEADINGS[field]}
              </th>
            ))}
            <th
              scope="col"
              className="whitespace-nowrap px-3 py-2.5 text-xs font-medium text-muted-foreground"
            >
              Source
            </th>
          </tr>
        </thead>
        <tbody>
          {leads.map((lead, rowIndex) => {
            const task = tasksByImage.get(lead.image_id);
            return (
              <motion.tr
                key={lead.id}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={staggerAt(rowIndex)}
                onClick={() => onSelect(lead)}
                className="cursor-pointer border-b border-border/60 transition-colors duration-150 last:border-0 hover:bg-accent/50"
              >
                <td className="px-3 py-2">
                  <img
                    src={api.thumbnailUrl(lead.image_id)}
                    alt=""
                    width={48}
                    height={32}
                    loading="lazy"
                    className="h-8 w-12 rounded border border-border object-cover"
                  />
                </td>

                {LEAD_FIELDS.map((field) => {
                  const value = lead[field];
                  const score = lead.confidence?.[field];
                  const uncertain =
                    value !== null && score !== undefined && score < LOW_CONFIDENCE;
                  const copyKey = `${lead.id}:${field}`;
                  const copyable = field === 'email' || field === 'phone';

                  return (
                    <td
                      key={field}
                      className={cn(
                        'max-w-[15rem] px-3 py-2 align-top',
                        uncertain && 'bg-warning-surface',
                        copyable && 'font-mono text-xs tnum',
                      )}
                    >
                      {value ? (
                        <span className="group/cell flex min-w-0 items-start gap-1">
                          <span className="min-w-0 break-words">{value}</span>
                          {copyable ? <button
                              type="button"
                              aria-label={`Copy ${HEADINGS[field]}`}
                              onClick={(event) => {
                                event.stopPropagation();
                                void copy(value, copyKey);
                              }}
                              className="mt-0.5 shrink-0 opacity-0 transition-opacity duration-150 group-hover/cell:opacity-100 focus-visible:opacity-100"
                            >
                              {copied === copyKey ? (
                                <Check className="size-3 text-success" aria-hidden="true" />
                              ) : (
                                <Copy className="size-3 text-muted-foreground" aria-hidden="true" />
                              )}
                            </button> : null}
                          {uncertain ? (
                            <InfoTip
                              label={
                                explainConfidence(field, lead) ??
                                'This value is worth checking against the card.'
                              }
                              className="mt-0.5 shrink-0"
                            >
                              <AlertTriangle
                                className="size-3 text-warning"
                                aria-hidden="true"
                              />
                            </InfoTip>
                          ) : null}
                        </span>
                      ) : (
                        // An em dash, not a blank cell: it shows the card
                        // genuinely did not print this field, rather than
                        // looking like a rendering gap.
                        <span
                          className="text-muted-foreground/40"
                          title="Not printed on this card"
                        >
                          —
                        </span>
                      )}
                    </td>
                  );
                })}

                <td className="px-3 py-2">
                  <div className="flex flex-col items-start gap-1">
                    <TierBadge task={task} />
                    {lead.is_duplicate_of ? <Badge
                        tone="outline"
                        title="Another card in this batch shares this email or phone"
                      >
                        duplicate
                      </Badge> : null}
                    {lead.edited_by_user ? <Badge tone="outline">edited</Badge> : null}
                  </div>
                </td>
              </motion.tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
