import { AnimatePresence, motion } from 'motion/react';
import { X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api } from '@/api/client';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Spinner } from '@/components/ui/Spinner';
import { drawerSlide, overlayFade } from '@/lib/motion';
import { LEAD_FIELDS, type Lead, type LeadField, type LeadUpdate } from '@/types/api';

type EditableField = LeadField | 'website';

const LABELS: Record<EditableField, string> = {
  first_name: 'First Name',
  last_name: 'Last Name',
  position: 'Position',
  company: 'Company',
  location: 'Location',
  phone: 'Phone',
  email: 'Email',
  website: 'Website',
};

const EDITABLE: EditableField[] = [...LEAD_FIELDS, 'website'];

// Field types that should not be spellchecked or autocorrected.
const NO_SPELLCHECK = new Set<EditableField>(['email', 'phone', 'website']);

interface Props {
  lead: Lead;
  onClose: () => void;
  onSave: (changes: LeadUpdate) => void;
  saving: boolean;
}

export function LeadDrawer({ lead, onClose, onSave, saving }: Props) {
  const [draft, setDraft] = useState<LeadUpdate>({});
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  // Discard a part-typed edit when a different card is opened, so a value
  // meant for one lead can never be saved onto another.
  useEffect(() => setDraft({}), [lead.id]);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
        return;
      }
      // Keep Tab inside the dialog: focus escaping to the page behind a modal
      // leaves keyboard users stranded.
      if (event.key !== 'Tab' || !panelRef.current) return;
      const focusable = panelRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      previouslyFocused?.focus();
    };
  }, [onClose]);

  const dirty = Object.keys(draft).length > 0;
  const displayName =
    [lead.first_name, lead.last_name].filter(Boolean).join(' ') || 'Unnamed Card';

  return (
    <AnimatePresence>
      <div className="fixed inset-0 z-50 flex justify-end">
        <motion.button
          type="button"
          aria-label="Close details"
          onClick={onClose}
          variants={overlayFade}
          initial="hidden"
          animate="visible"
          exit="exit"
          className="flex-1 bg-foreground/20 backdrop-blur-[2px]"
        />

        <motion.div
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-label={`Details for ${displayName}`}
          variants={drawerSlide}
          initial="hidden"
          animate="visible"
          exit="exit"
          className="flex w-full max-w-lg flex-col overflow-y-auto overscroll-contain border-l border-border bg-background shadow-2xl"
        >
          <header className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-border bg-background/95 px-5 py-4 backdrop-blur">
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold">{displayName}</h2>
              <p className="truncate text-xs text-muted-foreground">
                {lead.company ?? 'No company printed'}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              {lead.edited_by_user ? <Badge tone="outline">edited</Badge> : null}
              <Button
                ref={closeRef}
                variant="ghost"
                size="icon"
                onClick={onClose}
                aria-label="Close details"
              >
                <X className="size-4" aria-hidden="true" />
              </Button>
            </div>
          </header>

          <img
            src={api.imageUrl(lead.image_id)}
            alt={`Business card for ${displayName}`}
            width={1050}
            height={600}
            className="w-full border-b border-border bg-muted object-contain"
          />

          <div className="space-y-5 px-5 py-5">
            <div className="grid gap-3 sm:grid-cols-2">
              {EDITABLE.map((field) => {
                const current = draft[field] ?? lead[field] ?? '';
                const score =
                  field === 'website' ? undefined : lead.confidence?.[field];
                const inputId = `lead-${lead.id}-${field}`;
                return (
                  <div key={field}>
                    <label
                      htmlFor={inputId}
                      className="mb-1 flex items-center gap-1.5 text-xs font-medium text-muted-foreground"
                    >
                      {LABELS[field]}
                      {score !== undefined && score < 0.8 && (
                        <Badge tone="warning">check</Badge>
                      )}
                    </label>
                    <input
                      id={inputId}
                      name={field}
                      type={
                        field === 'email' ? 'email' : field === 'phone' ? 'tel' : 'text'
                      }
                      inputMode={field === 'phone' ? 'tel' : undefined}
                      autoComplete="off"
                      spellCheck={!NO_SPELLCHECK.has(field)}
                      value={current}
                      onChange={(event) =>
                        setDraft((previous) => ({
                          ...previous,
                          // An emptied field stores null, not "", so a cleared
                          // value reads as absent rather than blank.
                          [field]: event.target.value === '' ? null : event.target.value,
                        }))
                      }
                      // Suppressing the outline is only acceptable with a
                      // replacement that is just as visible; a 1px border
                      // colour change on its own is not.
                      className="w-full rounded-md border border-border bg-input px-2.5 py-1.5 text-sm transition-[border-color,box-shadow] duration-150 focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                    />
                  </div>
                );
              })}
            </div>

            {lead.notes ? <section>
                <h3 className="text-xs font-medium text-muted-foreground">
                  Notes From the Model
                </h3>
                <p className="mt-1.5 rounded-md bg-muted/60 p-2.5 text-xs">{lead.notes}</p>
              </section> : null}

            {lead.raw_text ? <section>
                <h3 className="text-xs font-medium text-muted-foreground">
                  Text Read From the Card
                </h3>
                <pre className="mt-1.5 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-muted/60 p-2.5 font-mono text-[11px]">
                  {lead.raw_text}
                </pre>
              </section> : null}

            {/* Boolean() is load-bearing: the bare expression evaluates to
                the number 0 when both lists are empty, and React renders a
                literal "0" into the page. */}
            {Boolean(lead.extra_phones?.length || lead.extra_emails?.length) && (
              <section>
                <h3 className="text-xs font-medium text-muted-foreground">
                  Also Printed on This Card
                </h3>
                <ul className="mt-1.5 space-y-1 text-xs">
                  {lead.extra_phones?.map((entry) => (
                    <li key={entry.number} className="flex items-center gap-2">
                      <span className="font-mono tnum">{entry.number}</span>
                      <span className="text-muted-foreground">{entry.type}</span>
                      {entry.validity && entry.validity !== 'valid' ? <Badge tone="outline" title="Not confirmed as a dialable number">
                          {entry.validity}
                        </Badge> : null}
                    </li>
                  ))}
                  {lead.extra_emails?.map((email) => (
                    <li key={email} className="font-mono">
                      {email}
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>

          <footer className="sticky bottom-0 mt-auto flex items-center justify-end gap-2 border-t border-border bg-background/95 px-5 py-3 backdrop-blur">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setDraft({})}
              disabled={!dirty || saving}
            >
              Reset
            </Button>
            <Button size="sm" onClick={() => onSave(draft)} disabled={!dirty || saving}>
              {saving ? <Spinner /> : null}
              {saving ? 'Saving…' : 'Save Corrections'}
            </Button>
          </footer>
        </motion.div>
      </div>
    </AnimatePresence>
  );
}
