/**
 * Wire types mirroring the backend's Pydantic schemas.
 *
 * Kept hand-written rather than generated so the shapes the UI depends on are
 * explicit and reviewable; the API contract is small and stable enough that a
 * generator would add a build step for little gain.
 */

export type JobStatus = 'queued' | 'processing' | 'completed' | 'failed';
export type TaskStatus = 'queued' | 'processing' | 'done' | 'failed';
export type ProviderTier = 'gpu' | 'cpu' | 'cloud';
export type OutputMode = 'json_schema' | 'json_object' | 'prompt_only' | 'repaired';

/** The seven fields the assignment requires, in display order. */
export const LEAD_FIELDS = [
  'first_name',
  'last_name',
  'position',
  'company',
  'location',
  'phone',
  'email',
] as const;

export type LeadField = (typeof LEAD_FIELDS)[number];

export interface Lead {
  id: string;
  image_id: string;
  first_name: string | null;
  last_name: string | null;
  position: string | null;
  company: string | null;
  location: string | null;
  phone: string | null;
  email: string | null;
  website: string | null;
  address: Record<string, string | null> | null;
  extra_phones: { number: string; type: string; validity?: string }[] | null;
  extra_emails: string[] | null;
  raw_text: string | null;
  notes: string | null;
  confidence: Partial<Record<LeadField, number>> | null;
  is_duplicate_of: string | null;
  edited_by_user: boolean;
  created_at: string;
}

export interface Task {
  id: string;
  image_id: string;
  status: TaskStatus;
  attempts: number;
  provider: ProviderTier | null;
  model: string | null;
  output_mode: OutputMode | null;
  latency_ms: number | null;
  error: string | null;
  original_filename: string | null;
}

export interface Job {
  id: string;
  status: JobStatus;
  total: number;
  done: number;
  failed: number;
  pending: number;
  created_at: string;
  updated_at: string;
  tasks: Task[];
  queue_depth: number;
  /** Null until a card has finished — the tiers differ too much to guess. */
  estimated_seconds_remaining: number | null;
}

export interface UploadRejection {
  filename: string;
  reason: string;
}

export interface JobCreated {
  job_id: string;
  accepted: number;
  duplicates: number;
  rejected: UploadRejection[];
}

export interface LeadUpdate {
  first_name?: string | null;
  last_name?: string | null;
  position?: string | null;
  company?: string | null;
  location?: string | null;
  phone?: string | null;
  email?: string | null;
  website?: string | null;
  notes?: string | null;
}

/** A batch is settled when nothing is left to process. */
export function isJobSettled(job: Job): boolean {
  return job.status === 'completed' || job.status === 'failed';
}
