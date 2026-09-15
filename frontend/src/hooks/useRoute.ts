import { useEffect, useState } from 'react';

/**
 * Hash-based routing.
 *
 * The app has two views, so a router dependency would cost more than it
 * saves. A hash keeps the batch URL shareable and survives a refresh, which
 * is the only routing requirement here.
 */
export function useRoute(): { jobId: string | null; go: (jobId: string | null) => void } {
  const [hash, setHash] = useState(() => window.location.hash);

  useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);

  const match = /^#\/jobs\/([0-9a-fA-F-]{36})$/.exec(hash);

  return {
    jobId: match ? match[1] : null,
    go: (jobId) => {
      window.location.hash = jobId ? `#/jobs/${jobId}` : '';
    },
  };
}
