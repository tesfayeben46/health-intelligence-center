/**
 * Theme resolver. The shell owns the design tokens (CSS custom properties in
 * styles/base.css); this reads the resolved values from :root and passes them
 * to every widget as ctx.theme. Widgets express layout in these tokens and
 * never hardcode a colour or font, so the palette changes in one place
 * (base.css).
 */
import type { Theme } from './types';

function cssVar(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** Two-stop amber sequential scale (light to deep), shared by funnels, heatmaps
 *  and choropleths. Token-driven, so a re-theme applies here too. */
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
      '900': cssVar('--navy-900', '#111827'),
      '800': cssVar('--navy-800', '#1b2230'),
      '700': cssVar('--navy-700', '#2b3344'),
      '600': cssVar('--navy-600', '#3a4456'),
      '500': cssVar('--navy-500', '#5a6478'),
      '50':  cssVar('--navy-50',  '#f3f1ee'),
    },
    status: {
      good:  cssVar('--good',  '#15865b'),
      watch: cssVar('--watch', '#9a6a3a'),
      alert: cssVar('--alert', '#c73545'),
      info:  cssVar('--navy-500', '#5a6478'),
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
    ramps: {
      scoreReliability: [cssVar('--score-low', '#EF4444'), cssVar('--score-mid', '#F59E0B'), cssVar('--score-high', '#22A06B')],
      diseaseBurden: [
        cssVar('--burden-1', '#B42318'), cssVar('--burden-2', '#EF4444'), cssVar('--burden-3', '#F59E0B'),
        cssVar('--burden-4', '#FDE68A'), cssVar('--burden-5', '#E8EEF7'), cssVar('--burden-6', '#E5E7EB'),
      ],
      anomalyZScore: [cssVar('--anomaly-lo', '#1D4ED8'), cssVar('--anomaly-mid', '#F8FAFC'), cssVar('--anomaly-hi', '#EF4444')],
      supplyStress: [
        cssVar('--stress-1', '#334155'), cssVar('--stress-2', '#64748B'), cssVar('--stress-3', '#94A3B8'),
        cssVar('--stress-4', '#CBD5E1'), cssVar('--stress-5', '#EEF2F7'),
      ],
      wastageTreemap: [
        cssVar('--wastage-1', '#1E293B'), cssVar('--wastage-2', '#334155'), cssVar('--wastage-3', '#475569'),
        cssVar('--wastage-4', '#64748B'), cssVar('--wastage-5', '#94A3B8'),
      ],
    },
  };
  return cached;
}

/** Drop the cache after a live re-theme so the next render re-reads tokens. */
export function invalidateTheme(): void { cached = null; }
