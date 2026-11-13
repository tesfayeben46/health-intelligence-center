/**
 * Fetch wrapper for /gw/audit/events, the Admin Audit page's data source.
 * Server-side paginated: each call returns one page of ops_audit.gateway_log
 * rows plus aggregate `stats` over the whole filtered set, so the KPI tiles
 * stay correct while paging.
 *
 * The admin /gw/{path} proxy prepends /api/ before forwarding, so the client
 * path must not include the api/ prefix.
 */
import { authFetch } from './useAuth';

const GW = '/gw/audit';

async function _json<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await authFetch(`${GW}${path}`, init);
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${body}`);
  }
  return r.json();
}

// One row of ops_audit.gateway_log joined to ops_identity."user". The actor is
// resolved to a real name via full_name, then username, then kc_sub. Every extra field
// (jwt_jti, byte_count, client_ip, user_agent, denied_reason, obligations) is
// surfaced only in the click-to-expand detail panel, not the table row.
export type AuditEvent = {
  id: number;
  ts: string;                        // timestamptz, ISO string
  kc_sub: string | null;             // actor uuid; null when unauthenticated
  username?: string | null;
  full_name?: string | null;         // preferred actor label (real name)
  email?: string | null;
  realm?: string | null;
  role_label?: string | null;
  jwt_jti?: string | null;
  method: string;
  path: string;
  mart_scope: string[];
  filters: Record<string, unknown>;
  row_count: number | null;
  byte_count?: number | null;
  latency_ms: number | null;
  http_status: number | null;
  policy_decision: 'allow' | 'deny' | 'error';
  policy_obligations?: Record<string, unknown> | null;
  denied_reason?: string | null;
  client_ip?: string | null;
  user_agent?: string | null;
};

export type AuditStats = {
  total: number;
  deny: number;
  error: number;
  http4xx: number;
  http5xx: number;
  auth_denied: number;
  avg_lat: number | null;
  max_lat: number | null;
  min_ts: string | null;
  max_ts: string | null;
};

export type AuditPage = {
  items: AuditEvent[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
  stats: AuditStats;
};

export type AuditQuery = {
  page?: number;
  pageSize?: number;
  q?: string;
  decision?: '' | 'allow' | 'deny' | 'error';
  method?: string;
  statusClass?: '' | '2xx' | '3xx' | '4xx' | '5xx';
  since?: string;   // ISO
  until?: string;   // ISO
  actorSub?: string;
  sort?: string;
  dir?: 'asc' | 'desc';
};

export function fetchAuditEvents(query: AuditQuery = {}): Promise<AuditPage> {
  const p = new URLSearchParams();
  p.set('page', String(query.page ?? 1));
  p.set('page_size', String(query.pageSize ?? 50));
  if (query.q)           p.set('q', query.q);
  if (query.decision)    p.set('decision', query.decision);
  if (query.method)      p.set('method', query.method);
  if (query.statusClass) p.set('status_class', query.statusClass);
  if (query.since)       p.set('since', query.since);
  if (query.until)       p.set('until', query.until);
  if (query.actorSub)    p.set('actor_sub', query.actorSub);
  if (query.sort)        p.set('sort', query.sort);
  if (query.dir)         p.set('dir', query.dir);
  return _json<AuditPage>(`/events?${p.toString()}`);
}
