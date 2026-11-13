/**
 * Alert surface client for /gw/alerts/* (gateway /api/alerts/*).
 *
 * `active`:  live firing/pending alerts from Prometheus (read-only).
 * `routing`: admin-managed routing rules (full CRUD) backed by
 *             ops_governance.alert_routing. A rule maps an alert selector
 *             (severity + match_label) to a delivery channel + recipient.
 *
 * The admin /gw/{path} proxy prepends /api/, so paths here omit it.
 */
import { authFetch } from './useAuth';

const GW = '/gw/alerts';

async function _json<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const r = await authFetch(input, init);
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${body}`);
  }
  return r.json();
}

export type Severity = 'critical' | 'warning' | 'info';

export type RoutingRule = {
  id: string;
  severity: Severity;
  match_label: string;
  channel: string;
  recipient: string;
  note: string;
  enabled: boolean;
  managed_by: 'seed' | 'runtime';
  updated_at: string | null;
};

export type RoutingRuleIn = {
  severity: Severity;
  match_label: string;
  channel: string;
  recipient: string;
  note?: string;
  enabled?: boolean;
};

export type ActiveAlert = {
  name: string;
  severity: string;
  service: string;
  state: string | null;
  active_since: string | null;
  summary: string;
};

export function listActive(): Promise<{ alerts: ActiveAlert[]; raw_count: number; source: string; error: string | null }> {
  return _json(`${GW}/active`);
}

export function listRouting(): Promise<RoutingRule[]> {
  return _json(`${GW}/routing`);
}

export function createRouting(body: RoutingRuleIn): Promise<RoutingRule> {
  return _json(`${GW}/routing`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function updateRouting(id: string, body: Partial<RoutingRuleIn>): Promise<RoutingRule> {
  return _json(`${GW}/routing/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function deleteRouting(id: string): Promise<{ id: string; deleted: boolean }> {
  return _json(`${GW}/routing/${id}`, { method: 'DELETE' });
}
