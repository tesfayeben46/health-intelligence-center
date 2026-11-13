/**
 * Dashboard permission-spec client for the admin SPA.
 *
 * Same catalog contract as internal-bi's api/spec.ts: the gateway resolves
 * ops_perm.{dashboard,page,widget,role_widget} per-caller and returns the
 * dashboard tree with each page's `widgets` list already grant-filtered.
 * The Sidebar keys catalog-gated nav items (admin/review, admin/ml-health)
 * to this spec: an item appears only when the caller's role is granted the
 * page's widget, so the SPA holds no role lists.
 *
 * Two deliberate differences from the internal-bi copy:
 *   · no 401-retry loop: the admin authFetch gates every /gw/* call on the
 *     session gate (see useAuth.ts), so the request never races the PKCE
 *     token exchange;
 *   · specAllowsWidget defaults to false with no spec (internal-bi defaults
 *     to true for its static-manifest fallback); a nav item must never flash
 *     for a role that was not granted it.
 *
 * Module-cached and single-flight: every caller shares one network fetch per
 * dashboard id.
 */
import { useEffect, useState } from 'react';
import { authFetch } from './useAuth';

export type ResolvedWidget = {
  id: string;
  title: string;
  kind: string;
  [k: string]: unknown;
};

export type ResolvedPage = {
  id: string;
  title: string;
  ordering: number;
  widgets: ResolvedWidget[];
  [k: string]: unknown;
};

export type ResolvedDashboard = {
  id: string;
  title: string;
  description: string;
  pages: ResolvedPage[];
  capabilities: string[];
  role_codes: string[];
  user_realm: string;
};

/** Sentinel: the gateway answered that this role has no access (401/403/404
 *  all mean "not accessible to your role"). Distinct from null (network or
 *  5xx), although for nav gating both mean "don't show the item". */
export const SPEC_DENIED = 'denied' as const;
export type SpecResult = ResolvedDashboard | typeof SPEC_DENIED | null;

const _cache = new Map<string, Promise<SpecResult>>();

async function _fetchSpec(dashboardId: string): Promise<SpecResult> {
  try {
    const r = await authFetch(`/gw/permissions/dashboard/${dashboardId}/spec`);
    if (r.status === 401 || r.status === 403 || r.status === 404) {
      console.warn(`[spec] dashboard ${dashboardId} denied for this role (HTTP ${r.status})`);
      return SPEC_DENIED;
    }
    if (!r.ok) {
      console.warn(`[spec] /gw/permissions/dashboard/${dashboardId}/spec: HTTP ${r.status}`);
      return null;
    }
    return await r.json();
  } catch (e) {
    console.warn('[spec] fetch error', e);
    return null;
  }
}

export function fetchDashboardSpec(dashboardId: string): Promise<SpecResult> {
  let p = _cache.get(dashboardId);
  if (!p) {
    p = _fetchSpec(dashboardId);
    _cache.set(dashboardId, p);
  }
  return p;
}

/** React hook: boots once (shared module cache), exposes spec + flags. */
export function useDashboardSpec(dashboardId: string) {
  const [spec, setSpec] = useState<ResolvedDashboard | null>(null);
  const [denied, setDenied] = useState(false);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let cancelled = false;
    fetchDashboardSpec(dashboardId).then(s => {
      if (cancelled) return;
      if (s === SPEC_DENIED) setDenied(true);
      else setSpec(s);
      setLoaded(true);
    });
    return () => { cancelled = true; };
  }, [dashboardId]);
  return { spec, loaded, denied };
}

/** Does the resolved spec grant `widgetId` on `pageId`?
 *  FALSE until a spec is loaded: catalog-gated nav items stay hidden
 *  rather than flashing for roles that were never granted them. */
export function specAllowsWidget(spec: ResolvedDashboard | null,
                                  pageId: string, widgetId: string): boolean {
  if (!spec) return false;
  const page = spec.pages.find(p => p.id === pageId);
  if (!page) return false;
  return page.widgets.some(w => w.id === widgetId);
}

/** Catalog page title (nav labels stay catalog-driven), with a fallback
 *  for the pre-load frame. */
export function specPageTitle(spec: ResolvedDashboard | null,
                               pageId: string, fallback: string): string {
  return spec?.pages.find(p => p.id === pageId)?.title || fallback;
}
