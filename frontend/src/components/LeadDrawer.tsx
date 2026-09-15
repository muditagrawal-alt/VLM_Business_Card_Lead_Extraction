import { X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { api } from '@/api/client';
import { LEAD_FIELDS, type Lead, type LeadField, type LeadUpdate } from '@/types/api';

const LABELS: Record<LeadField | 'website' | 'notes', string> = {
  first_name: 'First name',
  last_name: 'Last name',
  position: 'Position',
  company: 'Company',
  location: 'Location',
  phone: 'Phone',
  email: 'Email',
  website: 'Website',
  notes: 'Notes',
};

const EDITABLE = [...LEAD_FIELDS, 'website'] as const;

interface Props {
  lead: Lead;
  onClose: () => void;
  onSave: (changes: LeadUpdate) => void;
  saving: boolean;
}

export function LeadDrawer({ lead, onClose, onSave, saving }: Props) {
  const [draft, setDraft] = useState<LeadUpdate>({});

  // Discard any in-progress edit when a different card is opened, so a value
  // typed for one lead cannot be saved onto another.
  useEffect(() => setDraft({}), [lead.id]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const dirty = Object.keys(draft).length > 0;

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button
        type="button"
        aria-label="Close details"
        onClick={onClose}
        className="flex-1 bg-neutral-900/20"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Lead details"
        className="flex w-full max-w-lg flex-col overflow-y-auto bg-white shadow-xl"
      >
        <header className="flex items-start justify-between gap-4 border-b border-neutral-200 px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-neutral-900">
              {[lead.first_name, lead.last_name].filter(Boolean).join(' ') || 'Unnamed card'}
            </h2>
            <p className="text-xs text-neutral-500">{lead.company ?? 'No company printed'}</p>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close">
            <X className="size-4" aria-hidden />
          </Button>
        </header>

        <img
          src={api.imageUrl(lead.image_id)}
          alt="The business card this lead was extracted from"
          className="w-full border-b border-neutral-200 bg-neutral-50 object-contain"
        />

        <div className="space-y-4 px-5 py-4">
          <div className="grid gap-3 sm:grid-cols-2">
            {EDITABLE.map((field) => {
              const current = draft[field] ?? lead[field] ?? '';
              const score = lead.confidence?.[field as LeadField];
              return (
                <label key={field} className="block text-xs">
                  <span className="mb-1 flex items-center gap-1.5 font-medium text-neutral-600">
                    {LABELS[field]}
                    {score !== undefined && score < 0.8 && (
                      <span className="rounded bg-amber-100 px-1 text-[10px] text-amber-800">
                        check
                      </span>
                    )}
                  </span>
                  <input
                    value={current}
                    onChange={(event) =>
                      setDraft((previous) => ({
                        ...previous,
                        // An emptied field becomes null, not "", so a cleared
                        // value reads as absent rather than blank.
                        [field]: event.target.value === '' ? null : event.target.value,
                      }))
                    }
                    className="w-full rounded border border-neutral-300 px-2 py-1.5 text-sm focus:border-accent-500 focus:outline-none"
                  />
                </label>
              );
            })}
          </div>

          {lead.notes && (
            <section>
              <h3 className="text-xs font-medium text-neutral-600">Notes from the model</h3>
              <p className="mt-1 rounded bg-neutral-50 p-2 text-xs text-neutral-700">
                {lead.notes}
              </p>
            </section>
          )}

          {lead.raw_text && (
            <section>
              <h3 className="text-xs font-medium text-neutral-600">
                Text the model read from the card
              </h3>
              <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-neutral-50 p-2 font-mono text-[11px] text-neutral-700">
                {lead.raw_text}
              </pre>
            </section>
          )}

          {(lead.extra_phones?.length || lead.extra_emails?.length) && (
            <section className="text-xs">
              <h3 className="font-medium text-neutral-600">Also printed on this card</h3>
              <ul className="mt-1 space-y-0.5 text-neutral-700">
                {lead.extra_phones?.map((entry) => (
                  <li key={entry.number}>
                    {entry.number} <span className="text-neutral-400">({entry.type})</span>
                  </li>
                ))}
                {lead.extra_emails?.map((email) => <li key={email}>{email}</li>)}
              </ul>
            </section>
          )}
        </div>

        <footer className="mt-auto flex items-center justify-end gap-2 border-t border-neutral-200 px-5 py-3">
          <Button variant="ghost" size="sm" onClick={() => setDraft({})} disabled={!dirty}>
            Reset
          </Button>
          <Button size="sm" onClick={() => onSave(draft)} disabled={!dirty || saving}>
            {saving ? 'Saving…' : 'Save corrections'}
          </Button>
        </footer>
      </aside>
    </div>
  );
}
