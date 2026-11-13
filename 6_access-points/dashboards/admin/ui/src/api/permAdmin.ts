/**
 * Thin fetch wrappers around the gateway's /api/perm-admin/* routes.
 *
 * All calls go through the admin /gw/{path} proxy, which forwards to
 * nhdw-gateway with the caller's bearer (or the DEV_AUTH mock headers).
 */

// The admin /gw/{path} proxy prepends /api/ before forwarding, so the client
// path must not include the api/ prefix.
const GW = '/gw/perm-admin';

import { authFetch } from './useAuth';

async function _json<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const r = await authFetch(input, init);
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${body}`);
  }
  return r.json();
}

// catalog (1 bulk read populates every picker)
export type Catalog = {
  dashboards:       Array<{ id: string; title: string; description: string | null }>;
  pages:            Array<{ id: string; dashboard_id: string; title: string; ordering: number }>;
  widgets:          Array<{ id: string; title: string; kind: string; requires: any | null; description: string | null }>;
  filters:          Array<{ id: string; title: string; source: 'static' | 'mart_query' | 'ops_db_query'; source_ref: string | null; static_values: string[] | null }>;
  mart_attrs:       Array<{ schema_name: string; table_name: string; subject: string; data_class: string; granularity: string; default_sensitivity: string; description: string | null }>;
  identity_sources: Array<{ id: number; code: string; name: string; kind: string; protocol: string | null; status: 'active' | 'available' | 'disabled'; config_json: any | null; keycloak_alias: string | null; created_at: string | null; updated_at: string | null }>;
  roles:            Array<{ id: number; code: string; name: string; description: string | null; combine_strategy: string }>;
};

export function getCatalog(): Promise<Catalog> {
  return _json<Catalog>(`${GW}/catalog`);
}

// roles
export type RoleListItem = {
  id: number; code: string; name: string; description: string | null;
  combine_strategy: string;
  user_count: number;
  created_at: string | null; updated_at: string | null;
};

export function listRoles(): Promise<RoleListItem[]> {
  return _json<RoleListItem[]>(`${GW}/roles`);
}

export type RoleDetail = RoleListItem & {
  capabilities: string[];
  dashboards: string[];
  // getRole returns page-scoped widget grants ({dashboard_id, page_id, widget_id});
  // page_id is preserved so re-saving an untouched role never broadens a
  // page-specific grant to the whole dashboard.
  widgets: Array<{ dashboard_id: string; page_id?: string; widget_id: string }>;
  // Role-level data-scope caps (dimension -> values) from ops_perm.role_scope.
  scope: Record<string, string[]>;
  // Deprecated: the gateway returns []; role_global_filter/role_widget_filter no longer exist.
  global_filters: Array<{ page_id: string; filter_id: string; allowed_values: string[] | null }>;
  widget_filters: Array<{ dashboard_id: string; page_id?: string; widget_id: string; filter_id: string; allowed_values: string[] | null }>;
  users: Array<{ kc_sub: string; username: string; email: string | null; full_name: string | null; realm: string; source: string; granted_at: string | null; is_active: boolean }>;
};

export function getRole(id: number): Promise<RoleDetail> {
  return _json<RoleDetail>(`${GW}/roles/${id}`);
}

export type RoleIn = {
  code: string; name: string;
  description?: string | null;
  combine_strategy?: 'union' | 'intersection';
  capabilities?: string[];
};

export function createRole(body: RoleIn): Promise<{ id: number; code: string; name: string }> {
  return _json(`${GW}/roles`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function updateRole(id: number, body: RoleIn): Promise<{ ok: boolean; id: number }> {
  return _json(`${GW}/roles/${id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function deleteRole(id: number): Promise<{ ok: boolean }> {
  return _json(`${GW}/roles/${id}`, { method: 'DELETE' });
}

export type GrantsIn = {
  dashboards: string[];
  // page_id is OPTIONAL: omit it and the gateway expands the grant across every
  // page the widget is cataloged on (ops_perm.page_widget); pass it to pin the
  // grant to one page. The editor omits it for newly-checked widgets and keeps
  // the server's page_id for untouched ones.
  widgets: Array<{ dashboard_id: string; page_id?: string; widget_id: string }>;
  // Replace this role's runtime scope rows (null/omit = leave scope untouched).
  scope?: Record<string, string[]> | null;
  // Deprecated: accepted and ignored by the gateway.
  global_filters?: Array<{ page_id: string; filter_id: string; allowed_values: string[] | null }>;
  widget_filters?: Array<{ dashboard_id: string; page_id?: string; widget_id: string; filter_id: string; allowed_values: string[] | null }>;
};

export function replaceGrants(id: number, body: GrantsIn): Promise<{ ok: boolean; counts: Record<string, number>; revision_id: number }> {
  return _json(`${GW}/roles/${id}/grants`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// user-role assignments
export function grantUserRole(kc_sub: string, role_id: number) {
  return _json(`${GW}/user-roles`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kc_sub, role_id }),
  });
}

export function revokeUserRole(kc_sub: string, role_id: number) {
  return _json(`${GW}/user-roles?kc_sub=${encodeURIComponent(kc_sub)}&role_id=${role_id}`,
    { method: 'DELETE' });
}

// audit log
export type AuditRow = {
  id: number; ts: string;
  actor_sub: string | null; actor_username: string | null;
  revision_id: number | null; table_name: string; op: string;
  pk: any; before: any | null; after: any | null;
  revision_note: string | null;
};

export function auditLog(limit = 50): Promise<AuditRow[]> {
  return _json<AuditRow[]>(`${GW}/audit?limit=${limit}`);
}

// identity sources
export type IdentitySource = Catalog['identity_sources'][0];

export type IdentitySourceIn = {
  code: string; name: string;
  kind: 'direct' | 'oidc' | 'saml' | 'social' | 'fayda';
  protocol?: 'oidc' | 'saml' | null;
  status?: 'active' | 'available' | 'disabled';
  config_json?: any | null;
  keycloak_alias?: string | null;
};

export function listIdentitySources(): Promise<IdentitySource[]> {
  return _json<IdentitySource[]>(`${GW}/identity-sources`);
}

export function createIdentitySource(body: IdentitySourceIn) {
  return _json(`${GW}/identity-sources`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function updateIdentitySource(id: number, body: IdentitySourceIn) {
  return _json(`${GW}/identity-sources/${id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function deleteIdentitySource(id: number) {
  return _json(`${GW}/identity-sources/${id}`, { method: 'DELETE' });
}

// Dashboard Control: catalog tree + overrides + orphans
export type CtWidget = {
  id: string; title: string; kind: string; col_span: number; row_span: number;
  ordering: number; orphaned: boolean; widget_enabled: boolean; placement_enabled: boolean;
};
export type CtSection = {
  id: string; title: string; ordering: number; enabled: boolean;
  override: any | null; widgets: CtWidget[];
};
export type CtPage = {
  id: string; title: string; ordering: number; enabled: boolean; override: any | null;
  sections: CtSection[]; default_widgets: CtWidget[];
};
export type CtDashboard = {
  id: string; title: string; enabled: boolean; override: any | null; pages: CtPage[];
};

export function getCatalogTree(): Promise<{ dashboards: CtDashboard[] }> {
  return _json(`${GW}/catalog-tree`);
}

export type CatalogOverrideIn = {
  kind: 'dashboard' | 'page' | 'section' | 'widget' | 'placement';
  scope_id: string; enabled: boolean;
  overrides?: Record<string, unknown> | null; note?: string | null;
};
export function putCatalogOverride(body: CatalogOverrideIn): Promise<{ ok: boolean }> {
  return _json(`${GW}/catalog-override`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
}
export function deleteCatalogOverride(kind: string, scope_id: string): Promise<{ ok: boolean }> {
  return _json(`${GW}/catalog-override?kind=${encodeURIComponent(kind)}&scope_id=${encodeURIComponent(scope_id)}`,
               { method: 'DELETE' });
}

export type CatalogOrphan = {
  id: string; title: string; kind: string; orphaned_at: string;
  grant_count: number; placement_count: number;
};
export function listCatalogOrphans(): Promise<CatalogOrphan[]> {
  return _json(`${GW}/catalog-orphans`);
}
export function purgeCatalogOrphan(id: string): Promise<{ ok: boolean; purged: string }> {
  return _json(`${GW}/catalog-orphans/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

// per-user data scope (ops_identity.user_scope)
export function replaceUserScope(kc_sub: string, scope: Record<string, string[]>):
    Promise<{ ok: boolean; kc_sub: string; count: number }> {
  return _json(`${GW}/user-scope`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kc_sub, scope }),
  });
}
export function getUserScope(kc_sub: string):
    Promise<{ kc_sub: string; scope: Record<string, string[]> }> {
  return _json(`${GW}/user-scope/${encodeURIComponent(kc_sub)}`);
}
