import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { captureAccessCodeFromUrl, getAccessCode, setAccessCode } from './accessCode';

afterEach(() => {
  localStorage.clear();
  window.history.replaceState(null, '', '/');
  vi.restoreAllMocks();
});

describe('captureAccessCodeFromUrl', () => {
  it('stores the code and removes it from the address bar', () => {
    window.history.replaceState(null, '', '/?code=open-sesame#/jobs/abc');

    captureAccessCodeFromUrl();

    expect(getAccessCode()).toBe('open-sesame');
    // Gone from the URL, so it is not bookmarked or leaked in a Referer,
    // while the route in the hash is left alone.
    expect(window.location.search).toBe('');
    expect(window.location.hash).toBe('#/jobs/abc');
  });

  it('leaves a stored code alone when the link carries none', () => {
    setAccessCode('earlier');
    window.history.replaceState(null, '', '/');

    captureAccessCodeFromUrl();

    expect(getAccessCode()).toBe('earlier');
  });
});

describe('api requests', () => {
  function captureHeaders() {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('[]', { status: 200 }));
    return () => new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
  }

  it('send the access code when this browser has one', async () => {
    setAccessCode('open-sesame');
    const headers = captureHeaders();

    await api.getLeads('job');

    expect(headers().get('X-Access-Code')).toBe('open-sesame');
  });

  it('send no access code header when there is none', async () => {
    const headers = captureHeaders();

    await api.getLeads('job');

    expect(headers().has('X-Access-Code')).toBe(false);
  });

  it('keep the caller’s own headers alongside the code', async () => {
    setAccessCode('open-sesame');
    const headers = captureHeaders();

    await api.updateLead('lead', { company: 'Acme' });

    expect(headers().get('Content-Type')).toBe('application/json');
    expect(headers().get('X-Access-Code')).toBe('open-sesame');
  });
});
