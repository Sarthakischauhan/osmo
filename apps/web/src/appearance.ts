import { useEffect, useState } from 'react';
export type Theme = 'light' | 'dark';
const KEY = 'osmo.appearance.v1';
function initialTheme(): Theme {
  const forced = new URLSearchParams(window.location.search).get('theme');
  if (forced === 'light' || forced === 'dark') return forced;
  try {
    const saved = storedTheme(localStorage.getItem(KEY));
    if (saved) return saved;
  } catch { /* Storage can be unavailable in private browsing. */ }
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}
/** A stored value as a theme; anything else is not one. */
export function storedTheme(value: string | null): Theme | undefined {
  return value === 'light' || value === 'dark' ? value : undefined;
}
export function useAppearance() {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  useEffect(() => {
    document.documentElement.classList.toggle('light', theme === 'light');
    document.documentElement.classList.toggle('dark', theme === 'dark');
    try { localStorage.setItem(KEY, theme); } catch { /* The theme still works in memory. */ }
  }, [theme]);
  // Another window on this origin (the floating panel, the workspace) changed it: follow, so both always agree.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      const next = event.key === KEY ? storedTheme(event.newValue) : undefined;
      if (next) setTheme(next);
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);
  return [theme, setTheme] as const;
}
