/**
 * Thin fetch wrappers for /gw/policy/*, the Admin "Policies (OPA)" section's
 * data source. Two calls:
 *
 *   GET  /gw/policy/recent: last N rows of ops_audit.policy_log (real OPA
 *                               decisions logged by the enforcement path).
 *   POST /gw/policy/evaluate: ad-hoc what-if: runs the same opa.decide the
 *                               PEP uses and returns the full decision shape.
 *
 * The admin /gw/{path} proxy prepends /api/ before forwarding, so the paths
 * here must not include the api/ prefix.
 */
import { authFetch } from './useAuth';

const GW = '/gw/policy';

async function _json<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await authFetch(`${GW}${path}`, init);
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${body}`);
  }
  return r.json();
}

// GET /recent: rows of ops_audit.policy_log
// Columns come straight from the SELECT in routes/policy.py::recent_decisions.
export type PolicyDecisionRow = {
  id: number;
  ts: string;                       // timestamptz, ISO
  kc_sub: string | null;            // uuid of the principal, or null
  action: string;                   // verb: read | export | schedule | …
  resource: string;                 // subject / mart / artifact id
  decision: 'allow' | 'deny';
  rule_hits: string[];              // OPA rule paths that fired (text[])
  explanation: any | null;          // jsonb blob, may be null
};

export function fetchRecentDecisions(
  limit = 50,
  decision?: 'allow' | 'deny',
): Promise<PolicyDecisionRow[]> {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (decision) qs.set('decision', decision);
  return _json<PolicyDecisionRow[]>(`/recent?${qs.toString()}`);
}

// POST /evaluate: ad-hoc what-if
// Body accepted by routes/policy.py::evaluate: {action, resource, principal?}.
// `principal` (admin-only, requires view_ops_console) evaluates AS another
// identity; only the fields opa.decide consumes are honored.
export type EvaluatePrincipal = {
  primary_role?: string;
  roles?: string[];
  geographies?: string[];
  programs?: string[];
  purpose_of_use?: string;
};

export type EvaluateBody = {
  action: string;
  resource: string;
  principal?: EvaluatePrincipal;
};

// Response is the full Decision shape (see opa.py::Decision + the route).
export type PolicyDecision = {
  allow: boolean;
  reason: string;
  rule_hits: string[];
  obligations: Record<string, any>;
  masked_columns: string[];
  evaluated_for: {
    kc_sub: string;
    primary_role: string;
    roles: string[];
    geographies: string[];
    programs: string[];
  };
};

export function evaluatePolicy(body: EvaluateBody): Promise<PolicyDecision> {
  return _json<PolicyDecision>('/evaluate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
