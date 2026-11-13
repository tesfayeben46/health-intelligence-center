/**
 * Access-request admin client (review and provisioning).
 *
 * Like api/permAdmin.ts it goes through the admin /gw/{path} proxy, which
 * prepends /api/, so the client path must not include /api/. GW maps to the
 * gateway router /api/admin/access-requests.
 */
import { authFetch } from './useAuth';

const GW = '/gw/admin/access-requests';

async function _json<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const r = await authFetch(input, init);
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${body}`);
  }
  return r.json();
}

export type AccessRequestRow = {
  id: string;                 // uuid: the key for decide/provision
  tracking_id: string;        // AR-YYYY-NNNN: the human code
  submitted_at: string | null;
  full_name: string | null;
  email: string | null;
  phone: string | null;
  org: string | null;
  role: string | null;
  supervisor_email: string | null;
  irb: string | null;
  dashboards: string[];
  purpose: string | null;
  status: string;
  decision_at: string | null;
  decision_note: string | null;
  granted_role: string | null;
  granted_scope: any;         // jsonb: asyncpg may hand it back as a string
  provisioning_status: string | null;
  kc_user_id: string | null;
  fayda_id: string | null;
  verified_via: string | null;
};

export type Provisioning = {
  kc_sub: string; created: boolean; role: string | null;
  role_assigned: boolean; emailed: boolean; realm: string; username: string;
} | null;

export type DecideIn = {
  decision: 'approved' | 'declined' | 'info_requested';
  granted_role?: string | null;
  granted_scope?: Record<string, string[]> | null;
  note?: string | null;
};

export function listAccessRequests(status: string): Promise<AccessRequestRow[]> {
  return _json<AccessRequestRow[]>(`${GW}?status=${encodeURIComponent(status)}`);
}

export function decideAccessRequest(id: string, body: DecideIn): Promise<{
  id: string; tracking_id: string; status: string;
  provisioning_status: string | null; provisioning: Provisioning;
}> {
  return _json(`${GW}/${encodeURIComponent(id)}/decide`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function provisionAccessRequest(id: string): Promise<{
  id: string; provisioning_status: string; provisioning: Provisioning;
}> {
  return _json(`${GW}/${encodeURIComponent(id)}/provision`, { method: 'POST' });
}
