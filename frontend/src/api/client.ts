import { getAccessCode } from '@/lib/accessCode';
import type { Job, JobCreated, Lead, LeadUpdate, Task } from '@/types/api';

/**
 * Where the API lives. Empty means the same origin, as when Caddy serves the
 * SPA; a static host such as Vercel is built with VITE_API_BASE_URL set to the
 * API's own address, and the browser calls it directly.
 */
const API_ORIGIN = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '');
const BASE = `${API_ORIGIN}/api/v1`;

/** An API error carrying the backend's own message, which is user-facing. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly details?: { field: string; problem: string }[],
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // The deployment may require a shared passcode for anything that spends
  // inference or deletes data; sending it on every request is simpler than
  // tracking which routes are gated, and the server ignores it elsewhere.
  const headers = new Headers(init?.headers);
  const code = getAccessCode();
  if (code) headers.set('X-Access-Code', code);

  const response = await fetch(`${BASE}${path}`, { ...init, headers });

  if (!response.ok) {
    // The backend returns {error, details?}; fall back to the status text for
    // anything that failed before reaching it (a proxy error, for instance).
    let message = response.statusText || 'the request failed';
    let details: { field: string; problem: string }[] | undefined;
    try {
      const body = await response.json();
      if (typeof body?.error === 'string') message = body.error;
      if (Array.isArray(body?.details)) details = body.details;
    } catch {
      // Body was not JSON; the status-derived message stands.
    }
    throw new ApiError(message, response.status, details);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** The deployment asked for its access code, or refused the one it was sent. */
export function isAccessCodeError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

export const api = {
  createJob(files: File[], signal?: AbortSignal): Promise<JobCreated> {
    const form = new FormData();
    for (const file of files) form.append('files', file, file.name);
    // `signal ?? null` rather than passing undefined: under
    // exactOptionalPropertyTypes, fetch's RequestInit accepts null, not an
    // absent-but-declared property.
    return request<JobCreated>('/jobs', {
      method: 'POST',
      body: form,
      signal: signal ?? null,
    });
  },

  getJob(jobId: string): Promise<Job> {
    return request<Job>(`/jobs/${jobId}`);
  },

  getLeads(jobId: string): Promise<Lead[]> {
    return request<Lead[]>(`/jobs/${jobId}/leads`);
  },

  updateLead(leadId: string, changes: LeadUpdate): Promise<Lead> {
    return request<Lead>(`/leads/${leadId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(changes),
    });
  },

  retryTask(jobId: string, taskId: string): Promise<Task> {
    return request<Task>(`/jobs/${jobId}/tasks/${taskId}/retry`, { method: 'POST' });
  },

  deleteJob(jobId: string): Promise<void> {
    return request<void>(`/jobs/${jobId}`, { method: 'DELETE' });
  },

  /** Export URLs are plain links so the browser handles the download itself. */
  exportUrl(jobId: string, format: 'xlsx' | 'csv'): string {
    return `${BASE}/jobs/${jobId}/export.${format}`;
  },

  thumbnailUrl(imageId: string): string {
    return `${BASE}/images/${imageId}/thumb`;
  },

  imageUrl(imageId: string): string {
    return `${BASE}/images/${imageId}`;
  },
};
