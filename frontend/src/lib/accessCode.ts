const KEY = 'vlm-leads.access-code';

/**
 * The shared passcode this browser was given, if any.
 *
 * The deployment can require one for anything that spends inference or deletes
 * data. It is a single shared secret, not an account, so it lives in local
 * storage alongside the batch history and every access is guarded the same way.
 */
export function getAccessCode(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setAccessCode(code: string): void {
  try {
    localStorage.setItem(KEY, code.trim());
  } catch {
    // Without storage the code lasts for this page only, which still works.
  }
}

/**
 * Accept a code from the link it was shared in (`/?code=…`), then remove it
 * from the address bar so it is not bookmarked, screenshotted, or sent on to
 * another site in a Referer header.
 */
export function captureAccessCodeFromUrl(): void {
  const url = new URL(window.location.href);
  const code = url.searchParams.get('code');
  if (!code) return;
  setAccessCode(code);
  url.searchParams.delete('code');
  window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
}
