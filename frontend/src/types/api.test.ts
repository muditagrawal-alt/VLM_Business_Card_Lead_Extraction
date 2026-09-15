import { describe, expect, it } from 'vitest';
import { isJobSettled, LEAD_FIELDS } from './api';
import type { Job } from './api';

function job(status: Job['status']): Job {
  return {
    id: 'j',
    status,
    total: 1,
    done: 0,
    failed: 0,
    pending: 1,
    created_at: '',
    updated_at: '',
    tasks: [],
    queue_depth: 0,
    estimated_seconds_remaining: null,
  };
}

describe('isJobSettled', () => {
  it.each(['completed', 'failed'] as const)('treats %s as settled', (status) => {
    // Polling stops on this, so a wrong answer either hammers the API or
    // freezes the progress view.
    expect(isJobSettled(job(status))).toBe(true);
  });

  it.each(['queued', 'processing'] as const)('treats %s as unsettled', (status) => {
    expect(isJobSettled(job(status))).toBe(false);
  });
});

describe('LEAD_FIELDS', () => {
  it('is exactly the seven required columns, in order', () => {
    expect([...LEAD_FIELDS]).toEqual([
      'first_name',
      'last_name',
      'position',
      'company',
      'location',
      'phone',
      'email',
    ]);
  });
});
