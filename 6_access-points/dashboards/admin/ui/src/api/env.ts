/**
 * Thin fetch wrapper for /gw/observability/env, the admin Env widget's
 * data source. Returns deployment facts and the gateway's live feature-flag
 * state. Role-gated to R-OPS-* by the gateway.
 *
 * The admin /gw/{path} proxy prepends /api/ before forwarding, so the client
 * path must not include it: /gw/observability/env to GET /api/observability/env.
 */
import { authFetch } from './useAuth';

const GW = '/gw/observability';

async function _json<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await authFetch(`${GW}${path}`, init);
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${body}`);
  }
  return r.json();
}

export type EnvInfo = {
  name: string;
  namespace: string;
  base_domain: string;
  tls: string;
  gateway_version?: string;
};

export type FeatureFlag = {
  key: string;
  label: string;
  state: 'on' | 'off';
};

export type EnvResp = {
  environment: EnvInfo;
  feature_flags: FeatureFlag[];
};

export const fetchEnv = () => _json<EnvResp>('/env');
