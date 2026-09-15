import { useCallback, useEffect, useState } from 'react';

const KEY = 'vlm-leads.history';
const MAX_ENTRIES = 12;

export interface HistoryEntry {
  jobId: string;
  cards: number;
  at: string;
}

function read(): HistoryEntry[] {
  // Storage can throw outright in a private window or with site data blocked,
  // so every access is guarded and an empty history is a valid state.
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as HistoryEntry[]) : [];
  } catch {
    return [];
  }
}

/**
 * Remember this browser's recent batches.
 *
 * There are no accounts, so without this a reviewer who refreshes the page
 * loses the batch they just uploaded.
 */
export function useHistory() {
  const [entries, setEntries] = useState<HistoryEntry[]>([]);

  useEffect(() => {
    setEntries(read());
  }, []);

  const remember = useCallback((jobId: string, cards: number) => {
    setEntries((current) => {
      const next = [
        { jobId, cards, at: new Date().toISOString() },
        ...current.filter((entry) => entry.jobId !== jobId),
      ].slice(0, MAX_ENTRIES);
      try {
        localStorage.setItem(KEY, JSON.stringify(next));
      } catch {
        // A remembered list is a convenience; failing to persist it must not
        // interrupt the upload that just succeeded.
      }
      return next;
    });
  }, []);

  const forget = useCallback((jobId: string) => {
    setEntries((current) => {
      const next = current.filter((entry) => entry.jobId !== jobId);
      try {
        localStorage.setItem(KEY, JSON.stringify(next));
      } catch {
        /* see above */
      }
      return next;
    });
  }, []);

  return { entries, remember, forget };
}
