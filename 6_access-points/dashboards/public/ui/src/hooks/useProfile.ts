/**
 * useProfile: the country profile and this surface's copy deck.
 *
 * Everything country-specific on the page (masthead, headline, administrative
 * level names, the map) is data served by the gateway at /api/profile, proxied
 * same-origin. Switching country means a different profile directory on the
 * host, not a rebuild.
 *
 * The fetch starts at module load, not on mount, so it is in flight while React
 * boots and the above-the-fold masthead rarely swaps visibly from fallback text.
 *
 * If the gateway is unreachable or the profile is missing, `BUNDLED` renders,
 * so the page never waits on the network to show a masthead.
 */
import { useEffect, useState } from 'react';

export type Level = { code: string; singular: string; plural: string };

export type Profile = {
  active: string;
  label: string;
  country: { name: string; official_name: string; adjective: string; iso2: string; iso3: string;
             native?: { lang?: string; country_name?: string } };
  ministry: { abbr: string; name: string; native_name?: string; logo?: string };
  platform: { name: string; abbr: string; native_name?: string };
  geography: { levels: Level[]; admin1?: string; world?: string };
  brand: { accent: string };
  locale?: { default?: string; fiscal_calendar?: string;
             currency?: { code: string; symbol: string } };
  health_plan?: { abbr: string; name: string };
  /* Identity verification is a profile setting. et-v3 ships a Fayda block; a
     country with no national-ID verifier omits it and the access modal drops
     the verification card rather than posting to an endpoint that is absent. */
  identity?: {
    system: string;            // 'fayda': names the backend verifier
    card_title?: string;       // "Verify your identity with Fayda (optional)"
    help_text?: string;        // the explanatory line under the title
    id_label: string;          // "Fayda number (FAN)"
    id_hint?: string;          // the inline validation error wording
    id_pattern: string;        // a RegExp source the SPA compiles
    id_placeholder?: string;   // the input placeholder
    email_label?: string;      // "Email on your Fayda record"
  };
  contact?: { email?: string; phone?: string };
  geo_facts?: {
    bbox?: [number, number, number, number];
    centroid?: [number, number];
    globe_rotation_deg?: number;
    admin1_names?: string[];
    admin1_count?: number;
  };
};

/** The shape contract and the gateway-unreachable fallback. It is
 *  country-neutral, so a Zambia or Ghana deployment never shows Ethiopian
 *  chrome when the gateway is briefly unreachable. Mirrors the admin/workshop
 *  useProfile BUNDLED and the gateway's own _fallback_manifest: a plain,
 *  un-branded national warehouse, never one country's masthead. */
export const BUNDLED: Profile = {
  active: 'bundled',
  label: '',
  country: { name: '', official_name: '', adjective: '', iso2: '', iso3: '' },
  ministry: { abbr: '', name: 'Ministry of Health' },
  platform: { name: 'National Health Data Warehouse', abbr: 'NHDW' },
  geography: {
    levels: [
      { code: 'admin1', singular: 'Region', plural: 'Regions' },
      { code: 'admin2', singular: 'District', plural: 'Districts' },
    ],
  },
  brand: { accent: '#0374b8' },
  locale: { default: 'en', fiscal_calendar: 'gregorian' },
};

export type Copy = Record<string, any>;

/** The public deck as the shape contract and the gateway-unreachable fallback,
 *  used whenever the served one is unavailable or is missing a key. Keys mirror
 *  copy/public.json. Country-neutral for the same reason as BUNDLED. The
 *  country-specific wording lives in each profile's copy/public.json and is
 *  merged over this. */
export const BUNDLED_COPY: Copy = {
  govbar: {
    skip: 'Skip to content', mark_text: 'NHDW',
    mark_tag: 'National Health Data Warehouse',
    nav: { home: 'Home', data: 'Open data', articles: 'Articles' },
    request_access: 'Request access', dashboards: 'Dashboards',
  },
  hero: {
    strip_volume: 'Vol. 01', strip_series: 'Public Series',
    strip_owner: 'National Health Data Warehouse',
    eyebrow: 'Public health information service',
    headline_lead: 'National health data,', headline_accent: 'in one place.',
    headline_native: '',
    lede: 'A single trusted entrance to dashboards, open data, and the analytic consoles '
        + 'that power public-health decisions, from a national bulletin down to a single '
        + 'district.',
    trust: 'Privacy-protected national health data',
    trust_title: 'Aggregate and privacy-protected, k-anonymity at least 5, '
               + 'sourced from national health systems',
    cta_primary: 'Open the data catalogue', cta_secondary: 'Read the latest bulletins',
  },
  footer: {
    brand_line: 'National Health Data Warehouse · NHDW',
    owner_line: 'Ministry of Health',
    columns: [
      { title: 'About', links: ['Methodology', 'Governance', 'Contact'] },
      { title: 'Open', links: ['API docs', 'dbt docs', 'Code repository'] },
      { title: 'Legal', links: ['Privacy notice', 'Terms of use', 'Data licence'] },
    ],
  },
  // Access-request modal. The identity-verification card is gated on
  // profile.identity, not on copy; these are the surrounding labels + the
  // neutral placeholders a profile overrides with country-appropriate examples.
  access: {
    title: 'Request access',
    subtitle: 'Tell us who you are and what you need. We respond within 2 business days.',
    review_org: 'the platform administrators',
    agreement: 'By submitting, you accept the data use agreement and confirm you will not '
             + 'redistribute restricted data.',
    success_sub: 'Your request has been queued for review.',
    success_note: 'Keep this ID. You will receive an email confirmation, and the administrators '
                + 'will respond within 2 business days.',
    ph_name: 'Full name', ph_email: 'name@example.org', ph_phone: 'Phone number',
    ph_org: 'Organisation', ph_role: 'Programme officer, director, researcher',
  },
  // Articles ("newsroom") page masthead.
  articles_page: {
    eyebrow: 'Newsroom',
    title: 'News and health bulletins',
    sub: 'Surveillance bulletins, official press releases and public-health advisories, '
       + 'most recent first.',
  },
  // Audiences shown on the "Open a dashboard" launcher. Only the first is
  // country-flavoured (ministry / sub-national bodies); the others are neutral.
  dashboards: {
    bi_audience: 'Ministry staff · programme directors',
    admin_audience: 'IT operations · security officers',
    workshop_audience: 'Researchers · analysts · community contributors',
  },
};

type Loaded = { profile: Profile; copy: Copy };
let cache: Loaded | null = null;
const waiters = new Set<(v: Loaded) => void>();

/** Deep-merge served copy OVER the bundled deck, so a profile that omits a key
 *  inherits the English rather than rendering a blank. A partial deck is a
 *  legitimate thing to ship: most countries only need the masthead changed. */
function merge(base: any, over: any): any {
  if (over === undefined || over === null) return base;
  if (Array.isArray(base) || Array.isArray(over)) return over ?? base;
  if (typeof base !== 'object' || typeof over !== 'object') return over;
  const out: Record<string, any> = { ...base };
  for (const k of Object.keys(over)) out[k] = merge(base[k], over[k]);
  return out;
}

const ok = (o: any) => o && typeof o === 'object' && !o._unavailable && Object.keys(o).length > 0;

async function load(): Promise<Loaded> {
  let profile = BUNDLED;
  let copy = BUNDLED_COPY;
  try {
    const [p, c] = await Promise.all([
      fetch('/api/proxy/profile').then(r => (r.ok ? r.json() : null)).catch(() => null),
      fetch('/api/proxy/profile/copy/public').then(r => (r.ok ? r.json() : null)).catch(() => null),
    ]);
    if (ok(p) && p.country?.name) profile = merge(BUNDLED, p);
    if (ok(c)) copy = merge(BUNDLED_COPY, c);
  } catch {
    /* keep the bundled pair */
  }
  // The brand accent is a profile fact, and several things downstream read it
  // from CSS rather than from props: the hero globe picks up --accent to colour
  // its particles. Setting the variable here means one value in the profile
  // drives the whole surface, instead of the globe and the chrome drifting.
  try {
    if (profile.brand?.accent) {
      document.documentElement.style.setProperty('--accent', profile.brand.accent);
    }
  } catch { /* non-browser context (tests) */ }

  const loaded = { profile, copy };
  cache = loaded;
  waiters.forEach(w => w(loaded));
  waiters.clear();
  return loaded;
}

// in flight before React mounts
const inflight = load();

export function useProfile(): Loaded {
  const [state, setState] = useState<Loaded>(cache ?? { profile: BUNDLED, copy: BUNDLED_COPY });
  useEffect(() => {
    if (cache) { setState(cache); return; }
    let live = true;
    const onDone = (v: Loaded) => { if (live) setState(v); };
    waiters.add(onDone);
    inflight.then(onDone).catch(() => { /* fallback already showing */ });
    return () => { live = false; waiters.delete(onDone); };
  }, []);
  return state;
}

/** Name of an administrative level, for copy that has to say it inline:
 *  "region" in Ethiopia, "province" elsewhere. */
export function levelName(p: Profile, code: string, plural = false): string {
  const l = p.geography?.levels?.find(x => x.code === code);
  if (!l) return plural ? 'areas' : 'area';
  return plural ? l.plural : l.singular;
}

/** The finest administrative level this country reports at: Ethiopia's woreda,
 *  Zambia's district: for copy that names the aggregation floor ("aggregated to
 *  woreda or coarser"). Reads the LAST declared level so it is never hardcoded. */
export function finestLevel(p: Profile, plural = false): string {
  const levels = p.geography?.levels ?? [];
  const l = levels[levels.length - 1];
  if (!l) return plural ? 'areas' : 'area';
  return plural ? l.plural : l.singular;
}

/** The ministry crest, served from the active profile, or null when the profile
 *  declares none. A profile that ships no logo renders the word-mark instead:
 *  showing the previous country's crest under a new profile is worse than none.
 *  (Mirrors the admin/workshop logoUrl; the public SPA reaches the gateway
 *  through its same-origin /api/proxy prefix.) */
export function logoUrl(p: Profile): string | null {
  return p.ministry?.logo
    ? `/api/proxy/profile/asset/${encodeURIComponent(p.ministry.logo)}`
    : null;
}
