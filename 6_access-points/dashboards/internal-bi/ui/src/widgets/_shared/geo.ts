/**
 * Administrative geography for internal-BI map widgets (dashboard-scoped).
 *
 * The geometry comes from the active country profile and is registered with
 * echarts under a country-neutral map name. The display-name to polygon-name
 * mapping is resolved when the profile is built and shipped in reference.json,
 * so the browser matches on an exact string.
 *
 * The geometry is cached in module scope; call invalidateGeo() after a profile
 * swap so the next map re-fetches instead of drawing the previous country.
 */
import * as echarts from 'echarts';
import { REGION_GEO_NAME } from './reference';

/** echarts map names. Country-neutral: the geometry is whatever the active
 *  profile supplies. */
export const REGION_MAP = 'nhdw-regions';
export const REGION_CTX_MAP = 'nhdw-regions-ctx';

const REGION_URL = '/gw/profile/geo/admin1.geojson';
const WORLD_URL = '/gw/profile/geo/world-land-110m.geojson';

let _geo: any = null;
let _loading: Promise<any> | null = null;
let _ctxGeo: any = null;
let _ctxLoading: Promise<any> | null = null;

/** Drop the cached geometry. Call after a profile swap so the next map draws the
 *  new country instead of the one still sitting in module scope. */
export function invalidateGeo(): void {
  _geo = null; _loading = null; _ctxGeo = null; _ctxLoading = null;
}

/** Fetch and register the active profile's admin-1 geometry once. */
export function loadRegionGeo(): Promise<any> {
  if (_geo) return Promise.resolve(_geo);
  if (_loading) return _loading;
  _loading = fetch(REGION_URL)
    .then(r => {
      if (!r.ok) throw new Error(`admin-1 geometry unavailable (${r.status})`);
      return r.json();
    })
    .then(geo => { _geo = geo; echarts.registerMap(REGION_MAP, geo); return geo; })
    .catch(err => {
      // A map widget with no geometry renders its own empty state. Clearing the
      // in-flight promise lets a later attempt retry rather than caching a
      // permanent failure for the life of the page.
      _loading = null;
      console.warn('geo: could not load admin-1 geometry', err);
      throw err;
    });
  return _loading;
}

/** The admin-1 regions plus a faint surrounding-land context layer, merged into
 *  one FeatureCollection so both share a coordinate system and stay aligned.
 *  World-land features come first (drawn underneath), tagged
 *  `properties.__ctx = true` with a unique `name` (echarts needs distinct names);
 *  the region features are appended unchanged (drawn last, still interactive). */
export function loadRegionContextGeo(): Promise<any> {
  if (_ctxGeo) return Promise.resolve(_ctxGeo);
  if (_ctxLoading) return _ctxLoading;
  _ctxLoading = Promise.all([
    loadRegionGeo(),
    fetch(WORLD_URL).then(r => (r.ok ? r.json() : { features: [] })).catch(() => ({ features: [] })),
  ]).then(([regions, world]) => {
    const ctxFeats = ((world as any).features || []).map((f: any, i: number) => ({
      ...f,
      properties: { ...(f.properties || {}), __ctx: true, name: `ctx-${i}` },
    }));
    const merged: any = {
      type: 'FeatureCollection',
      features: [...ctxFeats, ...((regions as any).features || [])],
    };
    echarts.registerMap(REGION_CTX_MAP, merged);
    _ctxGeo = merged;
    return merged;
  }).catch(err => {
    _ctxLoading = null;
    console.warn('geo: could not build the context map', err);
    throw err;
  });
  return _ctxLoading;
}

/** Loose key for matching a display name against a polygon name.
 *
 *  Country-neutral: it strips punctuation and the trailing administrative
 *  designators that differ between sources. An entity model says "Oromia
 *  Region" and "Addis Ababa City Administration" where a boundary file says
 *  "Oromia" and "Addis Ababa"; Zambia's file says "North-Western" where the
 *  warehouse says "North Western". Transliteration differences are resolved at
 *  profile build time and arrive as an exact mapping instead. */
export const normRegion = (s: string): string => {
  const words = String(s || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .split(' ')
    .filter(Boolean);
  const SUFFIX = new Set(['region', 'city', 'administration', 'province', 'state',
                          'county', 'district', 'governorate', 'prefecture']);
  while (words.length > 1 && SUFFIX.has(words[words.length - 1])) words.pop();
  return words.join('');
};

/** The geojson feature name echarts should match for a given SPA region name.
 *
 *  Exact mapping first: the profile build resolves display name to polygon name,
 *  since it is the only step that sees both lists. The normalised scan is the
 *  fallback for a profile with no mapping. */
export function geoNameFor(geo: any, region: string): string {
  const exact = REGION_GEO_NAME[region];
  if (exact) return exact;
  const target = normRegion(region);
  const f = (geo?.features || []).find((x: any) => normRegion(x?.properties?.name) === target);
  return f ? f.properties.name : region;
}
