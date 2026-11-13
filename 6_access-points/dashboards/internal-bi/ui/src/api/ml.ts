/**
 * Thin fetch wrappers for /gw/ml/*: read-only views over the latest
 * MLflow runs + ops_ml.ml_predictions rows (the FLAT forecast schema),
 * used by the Predictions page.
 *
 * The internal-bi `/gw/{path}` proxy forwards to nhdw-gateway/api/{path},
 * so URLs here start with `/gw/ml/...`.
 */
import { authFetch } from './useAuth';

const GW = '/gw/ml';

async function _json<T>(path: string): Promise<T> {
  const r = await authFetch(`${GW}${path}`);
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${body}`);
  }
  return r.json();
}

export type RegisteredModel = {
  name: string;
  latest_version: string | null;
  champion_version: string | null;
  aliases: Record<string, string>;
  deployment_kind: string;
  schedule: string | null;
  enabled: boolean | null;
  last_updated: number | null;
};

export type MlRun = {
  id: string;
  model_version: string;
  deployment_kind: string;
  started_at: string;
  finished_at: string | null;
  status: 'running' | 'success' | 'failed';
  row_count: number | null;
  latency_ms: number | null;
  trigger_kind: 'schedule' | 'manual' | 'api';
  error_msg: string | null;
};

/** One row of the unified FLAT forecast schema (ops_ml.ml_predictions). */
export type MlPrediction = {
  predicted_at: string;
  model_version: string;
  disease: string;                       // '' for non-disease models
  district: string;                      // woreda uid; commodity for stockout
  year: number;
  week: number;
  pred_cases_mean: number | null;
  pred_cases_sd: number | null;
  pred_cases_lwr95: number | null;
  pred_cases_median: number | null;
  pred_cases_upr95: number | null;
  endemic_mean: number | null;
  endemic_sd: number | null;
  outbreak_z: number | null;
  alarm_threshold: number | null;
  alarm_prob: number | null;             // 0..1
  alarm_flag: number | null;             // 1 <=> alarm_prob >= 0.5
  n_draws: number | null;
  attrs: Record<string, any>;            // model-specific extras
};

export const listModels    = () => _json<{ models: RegisteredModel[]; count: number }>('');
export const listRuns      = (model: string, limit = 20) =>
    _json<{ model: string; runs: MlRun[] }>(`/${model}/runs?limit=${limit}`);
export const listPredictions = (model: string, limit = 200) =>
    _json<{ model: string; rows: MlPrediction[]; row_count: number }>(
        `/${model}/predictions?limit=${limit}`);
