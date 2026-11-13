/**
 * Data hook: the one way a widget gets its data, as mart rows through the
 * gateway (PEP to OPA to Trino) or null. There is no synthetic fallback.
 *
 * The gateway spec gives each widget a `data_url` (/api/permissions/widget/<id>/
 * data?...). The SPA reaches the gateway through its own `/gw/` proxy, so the
 * /api/ prefix is rewritten. A permitted stub, an empty result, a 403 (denied
 * at the mart) or any network error resolves to null, and the host draws an
 * empty state.
 */
import { authFetch } from '../api/useAuth';
import type { WidgetData } from './types';

function rowsToObjects(columns: any[], rows: any[]): Record<string, any>[] {
  const names = (columns || []).map(c => (typeof c === 'string' ? c : (c?.name ?? c?.id)));
  return (rows || []).map(r => Array.isArray(r)
    ? Object.fromEntries(names.map((n, i) => [n, r[i]]))
    : r);
}

const cache = new Map<string, Promise<WidgetData>>();

/** Fetch real rows for a widget's data_url (+ optional query params). Cached
 *  per (url + params) for the session. Resolves row objects or null. */
export function fetchWidgetData(
  dataUrl: string | undefined | null,
  params?: Record<string, string>,
): Promise<WidgetData> {
  if (!dataUrl) return Promise.resolve(null);
  const qs = Object.entries(params || {})
    .filter(([, v]) => v != null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join('&');
  const full = qs ? dataUrl + (dataUrl.includes('?') ? '&' : '?') + qs : dataUrl;
  const key = full;
  if (cache.has(key)) return cache.get(key)!;

  const p = authFetch(full.replace(/^\/api\//, '/gw/'))
    .then(r => (r.ok ? r.json() : null))
    .then(payload => {
      const d = payload?.data;
      if (!d || !Array.isArray(d.rows) || d.rows.length === 0) return null;
      return rowsToObjects(d.columns || [], d.rows);
    })
    .catch(() => null);
  cache.set(key, p);
  return p;
}

/** Drop cached data (e.g. after a filter change) so the next fetch is fresh. */
export function invalidateWidgetData(): void { cache.clear(); }

/*
 * Entity seam: the non-mart data path for CRUD / master-detail pages.
 *
 * Mart data goes through the widget_data OPA path (fetchWidgetData above), which
 * returns EMPTY STUBS for anything that isn't a governed mart. Entity data comes
 * from ops REST routes (`/gw/projects`, `/gw/artifacts`, `/gw/ml`, …) that are
 * Already authz-gated at their own gateway route: so this is a plain GET with
 * the same invariant: real rows or null (empty / denied / error:
 * null: the caller draws an empty state).
 */

/** Normalise the assorted shapes an ops endpoint may return to row objects, or
 *  null when there is no real data. Accepts a bare array, `{ rows }`, `{ items }`,
 *  `{ results }`, `{ data: [...] }`, or a mart-like `{ data: { columns, rows } }`. */
function normalizeEntityRows(payload: any): WidgetData {
  if (payload == null) return null;
  if (Array.isArray(payload)) return payload.length ? payload : null;
  const d = payload.data;
  if (d && Array.isArray(d.rows)) {
    return d.rows.length ? rowsToObjects(d.columns || [], d.rows) : null;
  }
  const arr = payload.rows ?? payload.items ?? payload.results ?? (Array.isArray(d) ? d : null);
  if (Array.isArray(arr)) return arr.length ? arr : null;
  return null;
}

const entityCache = new Map<string, Promise<WidgetData>>();

/** Fetch real rows from an ops REST endpoint (+ optional query params). Cached
 *  per (path + params) for the session, like fetchWidgetData. The path may be
 *  given as '/gw/projects', 'projects', '/projects', or an '/api/…' url: all
 *  resolve to the SPA's '/gw/…' gateway proxy. Resolves row objects or null. */
export function fetchEntity(
  path: string | undefined | null,
  params?: Record<string, string>,
): Promise<WidgetData> {
  if (!path) return Promise.resolve(null);
  // Route everything through the SPA's /gw/ proxy (to gateway /api/).
  let p = path.replace(/^\/api\//, '/gw/');
  if (!p.startsWith('/gw/')) p = '/gw/' + p.replace(/^\/+/, '');
  const qs = Object.entries(params || {})
    .filter(([, v]) => v != null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join('&');
  const full = qs ? p + (p.includes('?') ? '&' : '?') + qs : p;
  if (entityCache.has(full)) return entityCache.get(full)!;

  const pr = authFetch(full)
    .then(r => (r.ok ? r.json() : null))
    .then(normalizeEntityRows)
    .catch(() => null);
  entityCache.set(full, pr);
  return pr;
}

/** Drop cached entity data (e.g. after a create/update/delete) so the next
 *  fetchEntity re-reads the ops store. */
export function invalidateEntityData(): void { entityCache.clear(); }
