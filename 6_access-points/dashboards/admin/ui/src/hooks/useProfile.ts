/**
 * useProfile: the country this deployment is about.
 *
 * Everything that makes a page about one country (the ministry's name and
 * crest, what an administrative level is called, the word-mark in the corner)
 * is served by the gateway at /gw/profile. Changing country means a different
 * profile directory on the host, not a rebuild.
 *
 * Copied, not imported from a shared package: dashboards that need the same
 * helper each keep a copy, so independently released surfaces are not coupled
 * through a shared module.
 *
 * Two deliberate choices, shared with the public portal's version:
 *
 *   The fetch starts at module load, not on mount, so it is in flight while the
 *   app boots and the above-the-fold chrome does not visibly swap from fallback
 *   to real text.
 *
 *   There is always a fallback. If the gateway is unreachable the bundled values
 *   render: a generic word-mark still gives a working dashboard.
 */
import { useEffect, useState } from 'react';

export type Level = { code: string; singular: string; plural: string };

export type Profile = {
  active: string;
  label: string;
  country: { name: string; official_name: string; adjective: string };
  ministry: { abbr: string; name: string; logo?: string };
  platform: { name: string; abbr: string };
  geography: { levels: Level[] };
  locale?: { default?: string; fiscal_calendar?: string };
  /* The common copy deck, already resolved by the gateway. Kept whole rather
     than cherry-picked: a deck key this file does not know about is still
     reachable by the surface that does. */
  scope?: { all_geography?: string; all_programmes?: string; all_periods?: string;
            national?: string };
  workshop?: { footer_owner?: string };
  empty?: Record<string, string>;
};

/* Country-neutral by design: a fallback that names a country would show that
 * country on a deployment configured for another whenever the gateway is
 * briefly unreachable. */
export const BUNDLED: Profile = {
  active: 'bundled',
  label: '',
  country: { name: '', official_name: '', adjective: '' },
  ministry: { abbr: '', name: 'Ministry of Health' },
  platform: { name: 'National Health Data Warehouse', abbr: 'NHDW' },
  geography: {
    levels: [
      { code: 'admin1', singular: 'Region', plural: 'Regions' },
      { code: 'admin2', singular: 'District', plural: 'Districts' },
    ],
  },
  locale: { default: 'en', fiscal_calendar: 'gregorian' },
  scope: { all_geography: 'All areas' },
  workshop: {},
  empty: {},
};

let cache: Profile | null = null;
const waiters = new Set<(p: Profile) => void>();

function merge(base: any, over: any): any {
  if (over === undefined || over === null) return base;
  if (Array.isArray(base) || Array.isArray(over)) return over ?? base;
  if (typeof base !== 'object' || typeof over !== 'object') return over;
  const out: Record<string, any> = { ...base };
  for (const k of Object.keys(over)) out[k] = merge(base[k], over[k]);
  return out;
}

const ok = (o: any) => o && typeof o === 'object' && Object.keys(o).length > 0;

async function load(): Promise<Profile> {
  let profile = BUNDLED;
  try {
    const [p, c] = await Promise.all([
      fetch('/gw/profile').then(r => (r.ok ? r.json() : null)).catch(() => null),
      fetch('/gw/profile/copy/common').then(r => (r.ok ? r.json() : null)).catch(() => null),
    ]);
    if (ok(p) && p.country?.name) profile = merge(BUNDLED, p);
    if (ok(c)) profile = merge(profile, c);
  } catch { /* bundled stands */ }
  try {
    if ((profile as any).brand?.accent) {
      document.documentElement.style.setProperty('--accent', (profile as any).brand.accent);
    }
  } catch { /* non-browser context */ }
  cache = profile;
  waiters.forEach(w => w(profile));
  waiters.clear();
  return profile;
}

const inFlight = load();

export function useProfile(): Profile {
  const [p, setP] = useState<Profile>(cache || BUNDLED);
  useEffect(() => {
    if (cache) { setP(cache); return; }
    let live = true;
    const done = (v: Profile) => { if (live) setP(v); };
    waiters.add(done);
    inFlight.then(done).catch(() => {});
    return () => { live = false; waiters.delete(done); };
  }, []);
  return p;
}

/** The crest, when the profile ships one. A profile that declares no logo gets
 *  the word-mark instead: showing another country's crest is worse than none. */
export function logoUrl(p: Profile): string | null {
  return p.ministry?.logo ? `/gw/profile/asset/${encodeURIComponent(p.ministry.logo)}` : null;
}

/** What the geography wildcard is called, e.g. "All Ethiopia", "All Zambia". */
export const allGeography = (p: Profile): string =>
  p.scope?.all_geography || (p.country?.name ? `All ${p.country.name}` : 'All areas');

/** The name of an administrative level, by depth. Countries differ in how many
 *  levels they have, so a caller asks for "the second level" and gets this
 *  profile's name for it. */
export const levelName = (p: Profile, i: number, plural = false): string => {
  const l = p.geography?.levels?.[i];
  if (!l) return plural ? 'Areas' : 'Area';
  return plural ? l.plural : l.singular;
};


/* Non-React accessors. The widget runtime renders plain strings, so widgets
 * outside React still need these values and must not each add their own
 * fetch. They read the same module cache the hook fills, and fall back to the
 * bundled (country-neutral) value until it arrives. */
export const profileNow = (): Profile => cache || BUNDLED;
export const allGeographyNow = (): string => allGeography(profileNow());
export const levelNameNow = (i: number, plural = false): string =>
  levelName(profileNow(), i, plural);
