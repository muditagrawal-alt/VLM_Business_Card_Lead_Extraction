import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import { isJobSettled, type Job, type LeadUpdate } from '@/types/api';

/** Poll interval while a batch is still running. */
const POLL_MS = 2000;

/**
 * Follow a batch's progress.
 *
 * Polling rather than a server-sent stream: it behaves predictably behind a
 * reverse proxy, survives a sleeping laptop, and stops by itself once the
 * batch settles.
 */
export function useJob(jobId: string | null) {
  return useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.getJob(jobId as string),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const job = query.state.data as Job | undefined;
      return job && isJobSettled(job) ? false : POLL_MS;
    },
  });
}

export function useLeads(jobId: string | null, job: Job | undefined) {
  return useQuery({
    queryKey: ['leads', jobId, job?.done],
    queryFn: () => api.getLeads(jobId as string),
    enabled: Boolean(jobId),
    // Keyed on the completed count, so finished cards appear as they land
    // rather than only when the whole batch is done.
    placeholderData: (previous) => previous,
  });
}

export function useUpdateLead(jobId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ leadId, changes }: { leadId: string; changes: LeadUpdate }) =>
      api.updateLead(leadId, changes),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['leads', jobId] });
    },
  });
}

export function useRetryTask(jobId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) => api.retryTask(jobId as string, taskId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['job', jobId] });
    },
  });
}
