import { Copy, Cpu, Server, Cloud, AlertTriangle, Check } from 'lucide-react';
import { useState } from 'react';
import { cn } from '@/lib/utils';
import { LEAD_FIELDS, type Lead, type LeadField, type ProviderTier, type Task } from '@/types/api';

const HEADINGS: Record<LeadField, string> = {
  first_name: 'First name',
  last_name: 'Last name',
  position: 'Position',
  company: 'Company',
  location: 'Location',
  phone: 'Phone',
  email: 'Email',
};

/** Below this, a value is worth a human glance before it is used. */
const LOW_CONFIDENCE = 0.8;

const TIER_ICON: Record<ProviderTier, typeof Cpu> = {
  gpu: Server,
  cpu: Cpu,
  cloud: Cloud,
};

const TIER_LABEL: Record<ProviderTier, string> = {
  gpu: 'Processed on the self-hosted GPU model',
  cpu: 'Processed on the self-hosted CPU fallback model',
  cloud: 'Processed by the hosted Qwen API — this card left our server',
};

export function TierBadge({ task }: { task: Task | undefined }) {
  if (!task?.provider) return null;
  const Icon = TIER_ICON[task.provider];
  return (
    <span
      title={`${TIER_LABEL[task.provider]}${task.model ? ` (${task.model})` : ''}`}
      className={cn(
        'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide',
        task.provider === 'cloud'
          ? 'bg-amber-100 text-amber-800'
          : 'bg-neutral-100 text-neutral-600',
      )}
    >
      <Icon className="size-3" aria-hidden />
      {task.provider}
    </span>
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
      // Clipboard access can be denied; the value is still selectable.
    }
  };

  return (
    // The table scrolls inside its own container so the page never scrolls
    // sideways on a phone.
    <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white">
      <table className="w-full min-w-[980px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-neutral-200 bg-neutral-50 text-left">
            <th scope="col" className="w-14 px-3 py-2 font-medium text-neutral-600">
              Card
            </th>
            {LEAD_FIELDS.map((field) => (
              <th key={field} scope="col" className="px-3 py-2 font-medium text-neutral-600">
                {HEADINGS[field]}
              </th>
            ))}
            <th scope="col" className="px-3 py-2 font-medium text-neutral-600">
              Source
            </th>
          </tr>
        </thead>
        <tbody>
          {leads.map((lead) => {
            const task = tasksByImage.get(lead.image_id);
            return (
              <tr
                key={lead.id}
                onClick={() => onSelect(lead)}
                className="cursor-pointer border-b border-neutral-100 last:border-0 hover:bg-accent-50/40"
              >
                <td className="px-3 py-2">
                  <img
                    src={`/api/v1/images/${lead.image_id}/thumb`}
                    alt=""
                    loading="lazy"
                    className="h-8 w-12 rounded border border-neutral-200 object-cover"
                  />
                </td>

                {LEAD_FIELDS.map((field) => {
                  const value = lead[field];
                  const score = lead.confidence?.[field];
                  const uncertain = value !== null && score !== undefined && score < LOW_CONFIDENCE;
                  const copyKey = `${lead.id}:${field}`;

                  return (
                    <td
                      key={field}
                      className={cn(
                        'px-3 py-2 align-top',
                        uncertain && 'bg-amber-50',
                        field === 'email' && 'font-mono text-xs',
                      )}
                    >
                      {value ? (
                        <span className="group/cell inline-flex items-start gap-1">
                          <span className="break-words">{value}</span>
                          {(field === 'email' || field === 'phone') && (
                            <button
                              type="button"
                              aria-label={`Copy ${HEADINGS[field].toLowerCase()}`}
                              onClick={(event) => {
                                event.stopPropagation();
                                void copy(value, copyKey);
                              }}
                              className="mt-0.5 opacity-0 transition-opacity group-hover/cell:opacity-100 focus-visible:opacity-100"
                            >
                              {copied === copyKey ? (
                                <Check className="size-3 text-green-600" aria-hidden />
                              ) : (
                                <Copy className="size-3 text-neutral-400" aria-hidden />
                              )}
                            </button>
                          )}
                          {uncertain && (
                            <AlertTriangle
                              className="mt-0.5 size-3 shrink-0 text-amber-600"
                              aria-label="Worth checking"
                            />
                          )}
                        </span>
                      ) : (
                        // An em dash, not a blank: it shows the card genuinely
                        // did not print this field.
                        <span className="text-neutral-300" title="Not printed on this card">
                          —
                        </span>
                      )}
                    </td>
                  );
                })}

                <td className="px-3 py-2">
                  <div className="flex flex-col items-start gap-1">
                    <TierBadge task={task} />
                    {lead.is_duplicate_of && (
                      <span
                        title="Another card in this batch has the same email or phone"
                        className="rounded bg-purple-100 px-1.5 py-0.5 text-[10px] font-medium uppercase text-purple-800"
                      >
                        duplicate
                      </span>
                    )}
                    {lead.edited_by_user && (
                      <span className="text-[10px] uppercase tracking-wide text-neutral-400">
                        edited
                      </span>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
