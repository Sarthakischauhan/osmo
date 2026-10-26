import { useEffect, useState } from 'react';
export type Theme = 'light' | 'dark';
const KEY = 'osmo.appearance.v1';
function initialTheme(): Theme {
  const forced = new URLSearchParams(window.location.search).get('theme');
  if (forced === 'light' || forced === 'dark') return forced;
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === 'light' || saved === 'dark') return saved;
  } catch { /* Storage can be unavailable in private browsing. */ }
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}
export function useAppearance() {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  useEffect(() => {
    document.documentElement.classList.toggle('light', theme === 'light');
    document.documentElement.classList.toggle('dark', theme === 'dark');
    try { localStorage.setItem(KEY, theme); } catch { /* The theme still works in memory. */ }
  }, [theme]);
  return [theme, setTheme] as const;
}
