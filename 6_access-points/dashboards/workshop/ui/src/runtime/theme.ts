/**
 * Theme resolver: the shell owns the design tokens (CSS custom properties in
 * styles/base.css); this reads the resolved values off :root and hands them to
 * every widget as ctx.theme. Widgets carry layout-only CSS expressed in these
 * tokens and never hardcode a colour or font, so a palette change in base.css
 * reaches every widget.
 */
import type { Theme } from './types';

function cssVar(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** Two-stop amber sequential scale (light to deep), the shared colour language
 *  for funnels, heatmaps and choropleths. Token-driven, so it follows the theme. */
function makeAmber(): (t: number) => string {
  const lo = cssVar('--amber-lo', '#f6d9a6');
  const hi = cssVar('--amber-hi', '#8a4b12');
  const hex = (c: string) => {
    const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(c.replace('#', '').padStart(6, '0'));
    return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : [246, 217, 166];
  };
  const [r1, g1, b1] = hex(lo);
  const [r2, g2, b2] = hex(hi);
  return (t: number) => {
    const k = Math.max(0, Math.min(1, t));
    const r = Math.round(r1 + (r2 - r1) * k);
    const g = Math.round(g1 + (g2 - g1) * k);
    const b = Math.round(b1 + (b2 - b1) * k);
    return `rgb(${r},${g},${b})`;
  };
}

let cached: Theme | null = null;

export function resolveTheme(): Theme {
  if (cached) return cached;
  cached = {
    navy: {
      '900': cssVar('--navy-900', '#050b20'),
      '800': cssVar('--navy-800', '#0a1734'),
      '700': cssVar('--navy-700', '#334155'),
      '600': cssVar('--navy-600', '#475569'),
      '500': cssVar('--navy-500', '#64748B'),
      '50':  cssVar('--navy-50',  '#EEF2F7'),
    },
    status: {
      good:  cssVar('--good',  '#15865b'),
      watch: cssVar('--watch', '#9a6a3a'),
      alert: cssVar('--alert', '#c73545'),
      info:  cssVar('--navy-500', '#64748B'),
    },
    ink: [
      cssVar('--ink',   '#111827'),
      cssVar('--ink-2', '#374151'),
      cssVar('--ink-3', '#667085'),
      cssVar('--ink-4', '#9ca3af'),
    ],
    paper: cssVar('--paper', '#ffffff'),
    line:  cssVar('--line', '#e5e7eb'),
    fontSans: cssVar('--font-sans', 'Inter,system-ui,sans-serif'),
    fontMono: cssVar('--font-mono', 'JetBrains Mono,Menlo,monospace'),
    amber: makeAmber(),
  };
  return cached;
}

/** Drop the cache after a live re-theme so the next render re-reads tokens. */
export function invalidateTheme(): void { cached = null; }
