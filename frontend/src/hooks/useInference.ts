import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import { describeInference, type InferenceCopy } from '@/lib/inference';

/** What reads the cards on this deployment, as wording the page can show. */
export function useInference(): InferenceCopy {
  const { data } = useQuery({
    queryKey: ['readiness'],
    queryFn: () => api.readiness(),
    // The configured models do not change while a page is open.
    staleTime: 10 * 60_000,
    retry: false,
  });
  return useMemo(() => describeInference(data?.tiers), [data]);
}
