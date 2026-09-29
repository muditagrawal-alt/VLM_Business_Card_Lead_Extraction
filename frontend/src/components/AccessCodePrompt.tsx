import { useId, useState } from 'react';
import { KeyRound } from 'lucide-react';
import { Button } from '@/components/ui/Button';

interface Props {
  /** A code was sent and refused, as opposed to none having been given yet. */
  rejected: boolean;
  busy: boolean;
  onSubmit: (code: string) => void;
}

/**
 * Asks for the deployment's shared passcode when an upload is refused for the
 * lack of one. Shown inline where the error would have been, so the cards the
 * user already chose are sent as soon as the code is in.
 */
export function AccessCodePrompt({ rejected, busy, onSubmit }: Props) {
  const [code, setCode] = useState('');
  const inputId = useId();
  const hintId = useId();

  return (
    <form
      role="alert"
      aria-live="polite"
      onSubmit={(event) => {
        event.preventDefault();
        if (code.trim()) onSubmit(code);
      }}
      className="flex items-start gap-2.5 rounded-lg border border-border bg-card p-3.5 text-sm"
    >
      <KeyRound className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <label htmlFor={inputId} className="font-medium">
          This Demo Needs an Access Code
        </label>
        <p id={hintId} className="mt-0.5 text-xs text-muted-foreground">
          {rejected
            ? 'That code was not accepted. Check it against your invitation and try again.'
            : 'Enter the code from your invitation. This browser will remember it.'}
        </p>
        <div className="mt-2.5 flex gap-2">
          <input
            id={inputId}
            name="access-code"
            type="password"
            autoComplete="off"
            spellCheck={false}
            aria-describedby={hintId}
            aria-invalid={rejected || undefined}
            value={code}
            onChange={(event) => setCode(event.target.value)}
            className="w-full max-w-64 rounded-md border border-border bg-input px-2.5 py-1.5 text-sm transition-[border-color,box-shadow] duration-150 focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
          />
          <Button type="submit" size="sm" disabled={busy || !code.trim()}>
            {busy ? 'Checking…' : 'Continue'}
          </Button>
        </div>
      </div>
    </form>
  );
}
