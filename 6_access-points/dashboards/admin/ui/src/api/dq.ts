/**
 * Thin fetch wrappers for /gw/dq/*: the admin view of the dbt
 * artifacts (manifest, run_results, catalog) served by nhdw-gateway's
 * /api/dq/* routes. Same auth pattern as permAdmin.ts.
 */
import { authFetch } from './useAuth';

const GW = '/gw/dq';

async function _json<T>(path: string): Promise<T> {
  const r = await authFetch(`${GW}/${path}`);
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${body}`);
  }
  return r.json();
}

async function _post<T>(path: string, payload: unknown): Promise<T> {
  const r = await authFetch(`${GW}/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${body}`);
  }
  return r.json();
}

export type DqSummary = {
  total: number; pass: number; fail: number; warn: number; skip: number;
  runtime_s: number; generated_at: string | null;
  bands: Record<string, number>;
};

export type DqTest = {
  unique_id: string; status: string; execution_time: number | null;
  failures: number | null; message: string | null; compiled_at: string | null;
  acked?: boolean; acked_by?: string | null; acked_at?: string | null;
};

export type SourcePulse = {
  source: string; latest_period: string | null;
  completeness: number | null; validity: number | null;
  timeliness: number | null; reports: number | null;
};
export type SourcesResp = { sources: SourcePulse[]; expected: string[]; note: string | null };

export type TrendPoint = { day: string; rows: number };
export type IngestionSource = {
  source: string; ingested: boolean; last_ingest: string | null;
  records_last_cycle: number | null; lag_hours: number | null;
  sla_hours: number; status: string; changed_last_run: boolean | null;
  run_id: string | null; trend: TrendPoint[]; note?: string;
};
export type IngestionResp = { sources: IngestionSource[]; note: string | null };

export type Dimensions = {
  dimensions: Record<'completeness' | 'validity' | 'timeliness' | 'consistency' | 'uniqueness', number | null>;
  basis: Record<string, string>;
};

export type ReconCheck = {
  check: string; systems: string; conformed: number; matched: number;
  agreement_pct: number | null; sources: { source: string; count: number }[];
};
export type ReconResp = { checks: ReconCheck[]; note: string | null };

export type Compliance = {
  k_anon: {
    present: boolean; passed: boolean; status: string | null;
    failures: number | null; public_mart_count: number; public_marts: string[];
  };
};

export type AckResp = { ok: boolean; flag_id: string; acked_by: string; acked_at: string | null };

export type LineageNode = {
  id: string;
  name: string;
  tier: 'source' | 'staging' | 'vault_raw' | 'vault_business' | 'mart' | 'other';
  schema: string | null;
  test_status: 'pass' | 'fail' | 'warn' | 'untested' | null;
  tests_total: number;
  tests_failed: number;
  materialized: string | null;
  run_status?: string;
  description?: string;
};

export type LineageEdge = { from: string; to: string };

export type DqLineage = {
  nodes: LineageNode[];
  edges: LineageEdge[];
  as_of: string | null;
  tier_order: LineageNode['tier'][];
  note?: string;
};

export const getSummary  = () => _json<DqSummary>('summary');
export const getTests    = (status?: string, limit = 50) =>
    _json<DqTest[]>(`tests?limit=${limit}${status ? `&status=${status}` : ''}`);
export const getLineage  = () => _json<DqLineage>('lineage');
export const getSources        = () => _json<SourcesResp>('sources');
export const getIngestion      = () => _json<IngestionResp>('ingestion');
export const getDimensions     = () => _json<Dimensions>('dimensions');
export const getReconciliation = () => _json<ReconResp>('reconciliation');
export const getCompliance     = () => _json<Compliance>('compliance');

// incident queue + governance KPIs (durable history)
export type DqFlag = {
  id: string; raised_at: string; updated_at: string | null; resolved_at: string | null;
  raised_by: string; source: string; table_name: string; error_class: string;
  severity: string; description: string; status: string; events: number; acks: number;
};
export type FlagsResp = { flags: DqFlag[]; counts: Record<string, number>; limit: number; offset: number };
export type DqKpi = {
  open_by: { error_class: string; severity: string; source: string; n: number }[];
  raised_weekly: { week: string; raised: number }[];
  avg_hours_to_ack: number | null;
  avg_hours_to_resolve: number | null;
  resolved_total: number;
  reopened_fingerprints: number;
  steward: { backlog?: number; decisions?: number };
};
export type DetectResp = { findings: number; by_class: Record<string, number>; open_flags: number; latency_ms: number };
export const getFlags = (status = 'open', limit = 50, offset = 0) =>
    _json<FlagsResp>(`flags?status=${status}&limit=${limit}&offset=${offset}`);
export const getKpi   = () => _json<DqKpi>('kpi');
export const runDetect = () => _post<DetectResp>('detect', {});

export const ackTest = (t: {
  unique_id: string; model?: string; test?: string;
  message?: string | null; severity?: string; note?: string;
}) => _post<AckResp>('ack', t);
