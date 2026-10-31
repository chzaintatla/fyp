export type Theme = 'dark' | 'light';
const KEY = 'agentdesk_theme';

export function loadTheme(): Theme {
  try {
    return localStorage.getItem(KEY) === 'light' ? 'light' : 'dark';
  } catch {
    return 'dark';
  }
}

export function applyTheme(theme: Theme, accent?: string): void {
  const root = document.documentElement;
  root.dataset.theme = theme;
  // The character's colour is the accent only in the dark theme; the light theme keeps a darker, readable one.
  if (accent && theme === 'dark') root.style.setProperty('--accent', accent);
  else root.style.removeProperty('--accent');
  try {
    localStorage.setItem(KEY, theme);
  } catch { /* the theme just resets next time */ }
}

/** WCAG contrast ratio of two #rrggbb colours. */
export function contrast(a: string, b: string): number {
  const lum = (hex: string) => {
    const [r, g, bl] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
  };
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
