/**
 * Internal-BI reference tables and small pure helpers (dashboard-scoped).
 *
 * Helpers and reference tables are not shared across dashboards; each
 * dashboard keeps its own copy. Widgets import from here rather than from the
 * biApp engine, so a widget folder depends only on dashboard-scoped pure code
 * and its WidgetCtx.
 *
 * The tables below are fallbacks. `loadReference()` fills them from
 * /gw/profile/reference, which the gateway compiles from the entity model the
 * warehouse is built from, so the region list has a single source.
 *
 * They are populated in place (length = 0, then push) rather than reassigned,
 * because many modules hold a reference to these arrays and must see the
 * update. `main.tsx` awaits the load before the first render, so a widget never
 * sees a half-filled table.
 */

/* Empty: a fallback that names a country would render that
 * country's regions on a deployment configured for another one whenever the
 * gateway is unreachable. `main.tsx` awaits loadReference() before the first
 * render, so this state only appears when the gateway is down, and then no
 * filter options is preferable to the wrong country's. */
export const REGIONS: string[] = [];

/** Display name (and region code) to the name the boundary file uses, resolved
 *  at profile build time, the one step that sees both lists. Keyed by the
 *  formal name, the display name and the code so a caller holding any of them
 *  can resolve. */
export const REGION_GEO_NAME: Record<string, string> = {};

/** The same regions as {code,name}, the shape the filter chrome wants: the code
 *  is the value the marts key on (region_id) and the name is only a label.
 *  `biApp.ts` consumes this list rather than declaring its own. */
export const REGION_OPTIONS: Array<{ code: string; name: string }> = [];

/** Commodities, for the supply filters. Refilled from the profile; this list is
 *  the fallback, so the filter still populates when the gateway is unreachable.
 *  The code is the name here: the supply marts key on the display string
 *  rather than a short code, unlike regions and programmes. */
export const COMMODITIES: Array<{ code: string; name: string }> = [
  { code: 'Amlodipine 5mg', name: 'Amlodipine 5mg' },
  { code: 'Amoxicillin 250 DT', name: 'Amoxicillin 250 DT' },
  { code: 'Artemether-Lumefantrine', name: 'Artemether-Lumefantrine' },
  { code: 'BCG vaccine', name: 'BCG vaccine' },
  { code: 'COVID-19 vaccine', name: 'COVID-19 vaccine' },
  { code: 'Combined oral contracept.', name: 'Combined oral contracept.' },
  { code: 'Depo-Provera', name: 'Depo-Provera' },
  { code: 'Fortified blended food', name: 'Fortified blended food' },
  { code: 'GeneXpert cartridge', name: 'GeneXpert cartridge' },
  { code: 'HIV RDT kit', name: 'HIV RDT kit' },
  { code: 'HPV vaccine', name: 'HPV vaccine' },
  { code: 'IPV (inactivated polio)', name: 'IPV (inactivated polio)' },
  { code: 'Implanon NXT', name: 'Implanon NXT' },
  { code: 'Insulin · regular 100 IU', name: 'Insulin · regular 100 IU' },
  { code: 'Iron-folate', name: 'Iron-folate' },
  { code: 'Isoniazid 300mg', name: 'Isoniazid 300mg' },
  { code: 'LLIN bed net', name: 'LLIN bed net' },
  { code: 'Losartan 50mg', name: 'Losartan 50mg' },
  { code: 'Magnesium sulphate 50%', name: 'Magnesium sulphate 50%' },
  { code: 'Malaria RDT pf/pv', name: 'Malaria RDT pf/pv' },
  { code: 'Measles-Rubella', name: 'Measles-Rubella' },
  { code: 'Metformin 500mg', name: 'Metformin 500mg' },
  { code: 'OPV trivalent', name: 'OPV trivalent' },
  { code: 'ORS sachet', name: 'ORS sachet' },
  { code: 'Oxytocin 10 IU/ml', name: 'Oxytocin 10 IU/ml' },
  { code: 'PCV-13', name: 'PCV-13' },
  { code: 'Paracetamol 500mg', name: 'Paracetamol 500mg' },
  { code: 'Pediatric ART', name: 'Pediatric ART' },
  { code: 'Pentavalent DTwP-HepB-Hib', name: 'Pentavalent DTwP-HepB-Hib' },
  { code: 'RHZE fixed-dose combo', name: 'RHZE fixed-dose combo' },
  { code: 'RUTF (ready-to-use)', name: 'RUTF (ready-to-use)' },
  { code: 'Rotavirus vaccine', name: 'Rotavirus vaccine' },
  { code: 'TLD · adult ART', name: 'TLD · adult ART' },
  { code: 'Tetanus toxoid', name: 'Tetanus toxoid' },
];

/** Label of the geography filter's wildcard option ("All <country>"),
 *  replaced from the profile by loadReference(). */
export const scopeLabels = { allGeography: 'All areas' };

/** 2-letter mart region_id to SPA region name (matches MFR/IDSR org-unit codes). */
/* Empty for the same reason as REGIONS. `regionName` falls back to the id, so an
 * unresolved code shows as its code rather than as another country's region. */
export const REGION_BY_ID: Record<string, string> = {};
export const regionName = (id: string): string => REGION_BY_ID[id] || id || '-';

// profile presentation facts (country vocab, currency, health plan)
// Filled from the active profile's manifest (/gw/profile) alongside the reference
// tables, so widgets use the active country's vocabulary (admin-level labels,
// currency, national health-plan name). Empty until loaded; every consumer has a
// generic fallback, so a missing field or a failed load still renders
// country-neutral text.
export const COUNTRY = { name: '', adjective: '' };
export const CURRENCY = { code: '', symbol: '' };
export const HEALTH_PLAN = { abbr: '', name: '' };
let GEO_LEVELS_ORDERED: Array<{ code: string; singular: string; plural: string }> = [];
let GEO_LEVELS: Record<string, { singular: string; plural: string }> = {};
const _GENERIC_LEVELS: Record<string, [string, string]> = {
  admin1: ['Region', 'Regions'], admin2: ['Zone', 'Zones'],
  admin3: ['District', 'Districts'], admin4: ['Ward', 'Wards'],
};
/** The active profile's name for an admin level ('admin1'…'admin4'). A country
 *  that lacks the level (Zambia has no admin3) falls back to a neutral
 *  generic term. */
export function levelName(code: string, plural = false): string {
  const l = GEO_LEVELS[code];
  if (l) return plural ? l.plural : l.singular;
  const g = _GENERIC_LEVELS[code];
  return g ? (plural ? g[1] : g[0]) : code;
}
/** Format a monetary value already expressed in millions, in the active country's
 *  currency (supply "value at risk"). Falls back to a bare number when unset. */
export function moneyM(v: number): string {
  const sym = CURRENCY.symbol || CURRENCY.code;
  return sym ? `${sym} ${v}M` : `${Number(v).toLocaleString()}M`;
}
/** Prefix a label with the active country's plan abbreviation ("HSTP-II
 *  flagships"), or return the bare suffix ("flagships") when none is configured. */
export function planLabel(suffix: string): string {
  return HEALTH_PLAN.abbr ? `${HEALTH_PLAN.abbr} ${suffix}` : suffix;
}

// profile loading

/** Replace an array's contents, keeping its identity so existing imports see it. */
function refill<T>(target: T[], next: T[]): void {
  if (!next?.length) return;            // an empty profile table keeps the fallback
  target.length = 0;
  target.push(...next);
}

function rekey(target: Record<string, string>, next: Record<string, string>): void {
  if (!next || !Object.keys(next).length) return;
  for (const k of Object.keys(target)) delete target[k];
  Object.assign(target, next);
}

let loaded = false;

/**
 * Fill the tables from the active country profile. Awaited by main.tsx before
 * the first render.
 *
 * Never throws, and the reference fetch is bounded by `timeoutMs`: on any
 * failure the bundled tables stay in place, because a slightly stale filter
 * list is better than a dashboard that does not paint.
 */
export async function loadReference(timeoutMs = 4000): Promise<void> {
  if (loaded) return;
  loaded = true;
  // Presentation facts from the active profile's manifest (country vocabulary,
  // currency, national health-plan name). Independent of the reference fetch below.
  try {
    const pres = await fetch('/gw/profile');
    if (pres.ok) {
      const m = await pres.json();
      COUNTRY.name = m?.country?.name || COUNTRY.name;
      COUNTRY.adjective = m?.country?.adjective || COUNTRY.adjective;
      GEO_LEVELS_ORDERED = (m?.geography?.levels || []).map((l: any) => ({ code: l.code, singular: l.singular, plural: l.plural }));
      GEO_LEVELS = Object.fromEntries(GEO_LEVELS_ORDERED.map(l => [l.code, l]));
      if (m?.locale?.currency) { CURRENCY.code = m.locale.currency.code || ''; CURRENCY.symbol = m.locale.currency.symbol || ''; }
      if (m?.health_plan) { HEALTH_PLAN.abbr = m.health_plan.abbr || ''; HEALTH_PLAN.name = m.health_plan.name || ''; }
    }
  } catch { /* manifest optional; widgets fall back to country-neutral generics */ }
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    const res = await fetch('/gw/profile/reference', { signal: ctl.signal });
    clearTimeout(timer);
    if (!res.ok) return;
    const ref = await res.json();

    const regions: Array<{ code: string; name: string; geo_name?: string }> = ref.regions || [];
    if (regions.length) {
      // The SPA's region name is the short display form the boundary file uses
      // ("Oromia"), not the entity model's formal one ("Oromia Region"), which
      // is too long for a filter chip.
      refill(REGIONS, regions.map(r => r.geo_name || r.name));
      refill(REGION_OPTIONS, regions.map(r => ({ code: r.code, name: r.geo_name || r.name })));
      rekey(REGION_BY_ID, Object.fromEntries(
        regions.map(r => [r.code, r.geo_name || r.name])));
      // key on every form a caller might hold: the formal name from the entity
      // model, the short display name, and the region code.
      const geoMap: Record<string, string> = {};
      for (const r of regions) {
        const target = r.geo_name || r.name;
        if (!target) continue;
        geoMap[r.name] = target;
        geoMap[target] = target;
        if (r.code) geoMap[r.code] = target;
      }
      rekey(REGION_GEO_NAME, geoMap);
    }

    refill(PROGRAMMES, (ref.programmes || [])
      .filter((p: any) => p?.code)
      .map((p: any) => ({ code: p.code, name: p.name || p.code })));

    refill(COMMODITIES, (ref.commodities || [])
      .filter((c: any) => c?.code)
      .map((c: any) => ({ code: c.code, name: c.name || c.code })));

    refill(DISEASES, (ref.diseases || [])
      .filter((d: any) => d?.key)
      .map((d: any) => ({ key: d.key, name: d.name || d.key, climate: !!d.climate })));
    rekey(DISEASE_NAME, Object.fromEntries(DISEASES.map(d => [d.key, d.name])));

    // Wildcard label ("All <country>"), served already resolved by the gateway.
    try {
      const cres = await fetch('/gw/profile/copy/common');
      if (cres.ok) {
        const common = await cres.json();
        if (common?.scope?.all_geography) scopeLabels.allGeography = common.scope.all_geography;
      }
    } catch { /* label stays as bundled */ }
  } catch {
    /* bundled tables stand; see the doc comment */
  }
}

// {code,name}: value is the mart programme_id code (uppercase), name is the label.
// Mirrors biApp.ts PROGRAMMES so filter values ('MALARIA') match programme_id.
export const PROGRAMMES = [
  { code: 'HIV', name: 'HIV' },        { code: 'TB', name: 'TB' },
  { code: 'EPI', name: 'Immunization (EPI)' }, { code: 'MNCH', name: 'MNCH' },
  { code: 'NCD', name: 'NCD' },        { code: 'MALARIA', name: 'Malaria' },
  { code: 'FP', name: 'Family Planning' }, { code: 'NUTRITION', name: 'Nutrition' },
];

export interface Disease { key: string; name: string; climate: boolean; }
export const DISEASES: Disease[] = [
  { key: 'cholera', name: 'Cholera', climate: true }, { key: 'measles', name: 'Measles', climate: false },
  { key: 'awd', name: 'AWD', climate: true }, { key: 'malaria', name: 'Malaria', climate: true },
  { key: 'meningitis', name: 'Meningitis', climate: false }, { key: 'dengue', name: 'Dengue', climate: true },
  { key: 'yellowfever', name: 'Yellow fever', climate: true }, { key: 'pertussis', name: 'Pertussis', climate: false },
  { key: 'typhoid', name: 'Typhoid fever', climate: true }, { key: 'plague', name: 'Plague', climate: false },
  { key: 'rabies', name: 'Rabies', climate: false }, { key: 'tetanus', name: 'Neonatal tetanus', climate: false },
  { key: 'anthrax', name: 'Anthrax', climate: false }, { key: 'hepa', name: 'Hepatitis A', climate: true },
  { key: 'chikungunya', name: 'Chikungunya', climate: true }, { key: 'mpox', name: 'Mpox', climate: false },
  { key: 'covid19', name: 'COVID-19', climate: false }, { key: 'leishmaniasis', name: 'Visceral leishmaniasis', climate: true },
  { key: 'leptospirosis', name: 'Leptospirosis', climate: true }, { key: 'guineaworm', name: 'Guinea worm', climate: true },
];
export const DISEASE_NAME: Record<string, string> = Object.fromEntries(DISEASES.map(d => [d.key, d.name]));

export const PERIOD_OPTS: Record<string, string> = {
  last_4wk: 'Last 4 weeks', last_12wk: 'Last 12 weeks', last_6mo: 'Last 6 months',
  last_12mo: 'Last 12 months', last_24mo: 'Last 24 months', ytd: 'Year to date',
};
/** How many trailing weeks a period selects (client-side x-axis window). */
export const periodWeeks = (period: string): number =>
  ({ last_4wk: 4, last_12wk: 12, last_6mo: 26, last_12mo: 52, last_24mo: 52, ytd: 24, efy_ytd: 24 } as Record<string, number>)[period] || 52;

/** Is the current programme filter climate-sensitive (drives the climate band)? */
export function isClimateSensitive(programme: string): boolean {
  if (programme === 'all' || PROGRAMMES.some((p: { code: string }) => p.code === programme)) return true;
  const d = DISEASES.find(x => x.key === programme);
  return d ? d.climate : false;
}

/** Signal-chart UI model label to real ml run model id. */
export const signalModelId = (uiLabel: string): string =>
  ({ ears_c1: 'ears', farrington: 'outbreak', seasonal_naive: 'seasonal', ewars_csd: 'ewars_lite' } as Record<string, string>)[uiLabel] || 'ears';

/** '2026W9' / '2026-W09' to sortable ordinal (weekly periods aren't zero-padded). */
export function isoWeekOrd(w: string): number {
  const m = /^(\d{4})-?W(\d{1,2})$/.exec(String(w || ''));
  return m ? (+m[1]) * 100 + (+m[2]) : 0;
}
