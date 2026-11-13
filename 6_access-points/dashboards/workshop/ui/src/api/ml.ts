/**
 * Workshop ML-registry API client: backs the Models catalogue page.
 *
 * Backend lives at /api/ml in the gateway (routes/ml.py: alias-based MLflow
 * registry reads + ephemeral runner Jobs: no resident serving); workshop's
 * /gw/{path} proxy strips the `api/` prefix, so client paths begin /gw/ml.
 *
 * Vocabulary rules (adoption map, locked): model lifecycle is the
 * @champion/@challenger alias pair: never the deprecated MLflow stage
 * names. Ad-hoc prediction is ASYNC: POST …/predict returns a runner-Job id
 * that the caller polls via GET /gw/ml/jobs/{id} (~10 to 30 s). Display strings are formatted client-side from the
 * structured numbers the gateway returns: never pre-humanized.
 */
import { authFetch } from './useAuth';

const GW = '/gw/ml';

/** One registered model in GET /gw/ml: MLflow registry summary joined to
 *  its ops_ml.model_deployment row (schedule / enabled / kind). */
export type MlModelSummary = {
  name: string;
  latest_version: string | null;
  champion_version: string | null;
  aliases: Record<string, string>;        // alias name to version, e.g. {champion:"1"}
  deployment_kind: 'batch' | 'on_demand' | string;
  schedule: string | null;                // cron expression (informational)
  enabled: boolean | null;                // null = no deployment row
  last_updated: number | string | null;   // MLflow epoch milliseconds
};

/** One ops_ml.ml_run row (folded into the detail as last_run). */
export type MlRun = {
  id: string;
  model_version: string;
  deployment_kind: string;
  started_at: string | null;
  finished_at: string | null;
  status: 'running' | 'success' | 'failed' | string;
  row_count: number | null;
  latency_ms: number | null;
  trigger_kind: 'schedule' | 'manual' | 'api' | string;
  error_msg: string | null;
};

export type MlModelDetail = MlModelSummary & {
  model: string;
  description: string | null;             // package manifest description (registry)
  deployment: {
    model_name: string;
    model_alias: string;
    deployment_kind: string;
    schedule: string | null;
    enabled: boolean;
    params: Record<string, unknown> | null;
  } | null;
  last_run: MlRun | null;
};

/** pg numeric over JSON: some gateway numbers arrive as decimal STRINGS
 *  (observed on the live gateway); format with fmtNum, never assume number. */
export type Dec = number | string;

/** One field of a model's input/output SCHEMA: GET /gw/ml/{model}/schema.
 *  `type` is the MLflow signature type (string | long | double | datetime |
 *  boolean); `example` (inputs only) is the value from the model's
 *  input_example, used as a form placeholder. This is catalogue metadata: it
 *  describes the SHAPE a caller supplies / the model returns, never any stored
 *  prediction data. */
export type MlSchemaField = {
  name: string;
  type: string | null;
  required: boolean;
  example?: unknown;
};

/** GET /gw/ml/{model}/schema: the input/output contract of the serving
 *  (@champion, else latest) version. `published:false` means no signature was
 *  logged for this model; the UI shows a "schema not published" note. */
export type MlSchema = {
  model: string;
  version: string | null;
  published: boolean;
  inputs: MlSchemaField[];
  outputs: MlSchemaField[];
  input_example: Record<string, unknown>;
};

/** One MLflow version of a model, with its alias list.
 *  MLflow's model-versions/search does not populate per-version aliases:
 *  `aliases` comes back [] even when the version holds @champion. The
 *  authoritative alias to version map is the detail
 *  payload's `aliases`; merge it client-side when rendering versions. */
export type MlVersion = {
  version: string;
  aliases: string[];
  creation_timestamp: number | string | null;
  last_updated_timestamp: number | string | null;
  run_id: string | null;
  source: string | null;
  description: string | null;
};

/** POST /gw/ml/{model}/predict: job submitted, poll `poll`. */
export type MlPredictSubmit = {
  model: string;
  job_id: string;
  namespace: string;
  status: 'submitted' | string;
  input_uri: string;
  output_uri: string;
  poll: string;
};

/** GET /gw/ml/jobs/{id}: runner Job status; result_url is a presigned
 *  download for the output CSV once the Job succeeds. */
export type MlJobStatus = {
  job_id: string;
  model: string;
  mode: string | null;
  status: 'pending' | 'running' | 'succeeded' | 'failed' | string;
  active: number;
  succeeded: number;
  failed: number;
  start_time: string | null;
  completion_time: string | null;
  error: string | null;
  result_uri: string | null;
  result_url: string | null;
};

async function _json<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const r = await authFetch(input, init);
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${body}`);
  }
  return r.json();
}

export async function listModels(): Promise<MlModelSummary[]> {
  const j = await _json<{ models: MlModelSummary[]; count: number }>(GW);
  return j.models;
}

export function getModel(name: string): Promise<MlModelDetail> {
  return _json<MlModelDetail>(`${GW}/${encodeURIComponent(name)}`);
}

/** The model's input/output schema (catalogue metadata). Backs the workshop
 *  Models workbench: the typed run form (inputs) + the output-shape table.
 *  Never returns stored predictions: only the model's contract. */
export function getSchema(name: string): Promise<MlSchema> {
  return _json<MlSchema>(`${GW}/${encodeURIComponent(name)}/schema`);
}

export async function listVersions(name: string): Promise<MlVersion[]> {
  const j = await _json<{ model: string; versions: MlVersion[] }>(
    `${GW}/${encodeURIComponent(name)}/versions`);
  return j.versions;
}

/** Recent ops_ml.ml_run rows for a model (train + batch + ad-hoc). The
 *  predict widget reads these after a failed Job because the k8s Job
 *  condition only says "backoff limit reached": the runner's verbatim
 *  error (e.g. the signature-mismatch text) lands in ml_run.error_msg. */
export async function listRuns(name: string, limit = 10): Promise<MlRun[]> {
  const j = await _json<{ model: string; runs: MlRun[] }>(
    `${GW}/${encodeURIComponent(name)}/runs?limit=${limit}`);
  return j.runs;
}

/** Submit an ad-hoc prediction: the gateway stages the CSV to the object store and
 *  spawns a runner Job (predict-adhoc). Returns the job id: poll getJob. */
export function submitPredict(
  name: string,
  body: { csv: string } | { rows: Record<string, unknown>[] },
): Promise<MlPredictSubmit> {
  return _json<MlPredictSubmit>(`${GW}/${encodeURIComponent(name)}/predict`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function getJob(jobId: string): Promise<MlJobStatus> {
  return _json<MlJobStatus>(`${GW}/jobs/${encodeURIComponent(jobId)}`);
}

/* client-side formatting from structured gateway values */

/** MLflow epoch-milliseconds to local date string. */
export function fmtEpochMs(v: number | string | null | undefined): string {
  if (v == null) return 'n/a';
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return 'n/a';
  return new Date(n).toLocaleDateString(undefined,
    { year: 'numeric', month: 'short', day: 'numeric' });
}

/** ISO / pg-text timestamp to local date + time. */
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return 'n/a';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined,
    { year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit' });
}

export function fmtNum(n: Dec | null | undefined, digits = 1): string {
  if (n == null || !Number.isFinite(Number(n))) return 'n/a';
  return Number(n).toLocaleString(undefined,
    { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

/** Pill class for an alias badge: @champion is the serving alias. */
export function aliasPillClass(alias: string): string {
  return alias === 'champion' ? 'good'
       : alias === 'challenger' ? 'watch'
       : 'neutral';
}
