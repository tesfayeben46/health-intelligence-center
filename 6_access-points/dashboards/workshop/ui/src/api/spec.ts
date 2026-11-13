/**
 * Dashboard permission-spec client (workshop copy of the internal-bi client:
 * copied, never imported across dashboards, per the repetition-over-shared-
 * coupling rule).
 *
 * Loads the resolved dashboard tree from the gateway. Used by the shell
 * (sidebar nav from spec.pages) and the runtime PageRenderer (sections:
 * widget grid). The workshop backend's /gw/{path} proxy forwards to
 * nhdw-gateway/api/{path}, so client paths start with /gw/permissions.
 */
import { useEffect, useState } from 'react';
import { authFetch } from './useAuth';

export type ResolvedFilter = {
  id: string;
  title: string;
  source: 'static' | 'mart_query' | 'ops_db_query';
  allowed_values: string[] | null;     // null = wildcard
  values_url: string;
};

export type ResolvedWidget = {
  id: string;
  title: string;
  kind: string;
  requires: any | null;
  local_filters: ResolvedFilter[];
  data_url: string;
  // Section-catalog placement (ops_perm.page_widget): resolved per-role.
  section_id?: string | null;
  col_span?: number;   // grid columns this widget spans (of section.columns)
  row_span?: number;
  placement?: string | null;   // 'chrome.*' widgets render in chrome slots, not the grid
  // Plugin widgets: source='plugin': the shell dynamically imports the
  // same-origin bundle_url (a reviewed Web Component) and mounts component_type,
  // re-rendering on reacts_to dims. Core widgets: source='core', the rest absent.
  source?: string;
  component_type?: string | null;
  reacts_to?: string[] | null;
  bundle_url?: string | null;
};

/** A titled layout band within a page (ops_perm.section), resolved per-role.
 *  Empty sections are dropped by the gateway; widgets is grant-filtered. */
export type ResolvedSection = {
  id: string;
  title: string;
  eyebrow?: string | null;
  description?: string | null;
  ordering: number;
  columns: number;     // grid track count (default 12)
  dense: boolean;      // grid-auto-flow dense, reflowing to fill permission gaps
  widgets: ResolvedWidget[];
};

export type ResolvedPage = {
  id: string;
  title: string;
  ordering: number;
  global_filters: ResolvedFilter[];
  widgets: ResolvedWidget[];
  sections: ResolvedSection[];
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

/** Sentinel: the gateway ANSWERED and said this role has no access (401/403/404
 *  all mean "not accessible to your role" here). Distinct from null (network /
 *  5xx). Neither ever triggers a synthetic fallback: the shell shows an
 *  explicit denied / unavailable state. */
export const SPEC_DENIED = 'denied' as const;
export type SpecResult = ResolvedDashboard | typeof SPEC_DENIED | null;

export async function fetchDashboardSpec(dashboardId: string): Promise<SpecResult> {
  try {
    // PKCE races the app boot: the token lands asynchronously after the
    // Keycloak redirect, so a 401 here usually means "not authenticated YET",
    // not "denied". Wait for the token bridge (window.__nhdwAccessToken) up to
    // ~10s, retrying; only a 401 with a token attached (or a 403/404) is a
    // real denial.
    for (let attempt = 0; attempt < 20; attempt++) {
      const hadToken = typeof window !== 'undefined' && !!window.__nhdwAccessToken;
      const r = await authFetch(`/gw/permissions/dashboard/${dashboardId}/spec`);
      if (r.status === 401 && !hadToken) {
        await new Promise(res => setTimeout(res, 500));
        continue;
      }
      if (r.status === 401 || r.status === 403 || r.status === 404) {
        console.warn(`[spec] dashboard ${dashboardId} denied for this role (HTTP ${r.status})`);
        return SPEC_DENIED;
      }
      if (!r.ok) {
        console.warn(`[spec] /gw/permissions/dashboard/${dashboardId}/spec: HTTP ${r.status}`);
        return null;
      }
      return await r.json();
    }
    console.warn(`[spec] no token arrived after retries: treating as unauthenticated (denied)`);
    return SPEC_DENIED;
  } catch (e) {
    console.warn('[spec] fetch error', e);
    return null;
  }
}

/** React hook: boots once, exposes the spec + loading + denied flags. */
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
