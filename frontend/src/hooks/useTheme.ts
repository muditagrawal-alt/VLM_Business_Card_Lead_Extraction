import { useCallback, useEffect, useState } from 'react';

const KEY = 'vlm-leads.theme';
export type Theme = 'light' | 'dark' | 'system';

function read(): Theme {
  try {
    const stored = localStorage.getItem(KEY);
    return stored === 'light' || stored === 'dark' ? stored : 'system';
  } catch {
    return 'system';
  }
}

/**
 * Light, dark, or follow the operating system.
 *
 * "system" writes no attribute at all, leaving the stylesheet's
 * prefers-color-scheme media query to decide — which means a user who changes
 * their OS theme sees the app follow without reloading.
 */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>('system');

  useEffect(() => setTheme(read()), []);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'system') {
      root.removeAttribute('data-theme');
    } else {
      root.setAttribute('data-theme', theme);
    }

    // Match the browser chrome to the page so the notch area and scrollbars
    // do not flash the wrong colour.
    const dark =
      theme === 'dark' ||
      (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute('content', dark ? '#0a0a0a' : '#ffffff');

    try {
      if (theme === 'system') localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, theme);
    } catch {
      // A remembered preference is a convenience, not a requirement.
    }
  }, [theme]);

  const cycle = useCallback(
    () => setTheme((current) => (current === 'dark' ? 'light' : 'dark')),
    [],
  );

  return { theme, setTheme, cycle };
}
