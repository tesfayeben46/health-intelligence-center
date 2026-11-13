/**
 * Predictions page · surfaces the latest output of NHDW's batch ML
 * models from ops_ml.ml_predictions (the FLAT forecast schema, filled
 * by the weekly Airflow DAG's runner Jobs). Auto-refreshes every 60s.
 * Read-only: the admin dashboard owns retraining + @champion promotion.
 *
 * Model coverage (registry keys):
 *   ears               · endemic-channel alarms (mean + k·sd baseline)
 *   outbreak           · Farrington-style threshold breaches
 *   seasonal           · seasonal-profile forecasts + intervals
 *   ewars_lite         · negative-binomial GLM with climate lags
 *   stockout_forecast  · at-risk commodities (district = commodity)
 */
import { useEffect, useMemo, useState } from 'react';
import * as ml from '../api/ml';

type LoadState = {
  models: ml.RegisteredModel[];
  runs: Record<string, ml.MlRun[]>;
  preds: Record<string, ml.MlPrediction[]>;
};

function fmtAgo(iso: string | null): string {
  if (!iso) return 'n/a';
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000)     return `${Math.round(ms / 1000)}s ago`;
  if (ms < 3_600_000)  return `${Math.round(ms / 60_000)}m ago`;
  if (ms < 86_400_000) return `${Math.round(ms / 3_600_000)}h ago`;
  return `${Math.round(ms / 86_400_000)}d ago`;
}

function num(v: unknown, digits = 2): string {
  if (v === null || v === undefined || v === '') return 'n/a';
  const n = typeof v === 'number' ? v : Number(v);
  if (!Number.isFinite(n)) return 'n/a';
  return n.toFixed(digits);
}

function pct(v: unknown): string {
  if (v === null || v === undefined || v === '') return 'n/a';
  const n = typeof v === 'number' ? v : Number(v);
  if (!Number.isFinite(n)) return 'n/a';
  return `${(n * 100).toFixed(0)}%`;
}

/** ISO week label, e.g. 2026-W23. */
function wk(r: ml.MlPrediction): string {
  return `${r.year}-W${String(r.week).padStart(2, '0')}`;
}

export default function Predictions() {
  const [data, setData] = useState<LoadState | null>(null);
  const [err, setErr]   = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const { models } = await ml.listModels();
        const names = models.map(m => m.name);
        const [runArr, predArr] = await Promise.all([
          Promise.all(names.map(n => ml.listRuns(n, 5).catch(() => ({ model: n, runs: [] })))),
          Promise.all(names.map(n => ml.listPredictions(n, 200).catch(() => ({ model: n, rows: [], row_count: 0 })))),
        ]);
        if (cancelled) return;
        const runs:  Record<string, ml.MlRun[]> = {};
        const preds: Record<string, ml.MlPrediction[]> = {};
        runArr.forEach(r  => { runs[r.model]  = r.runs;  });
        predArr.forEach(p => { preds[p.model] = p.rows; });
        setData({ models, runs, preds });
        setErr(null);
      } catch (e: any) {
        if (!cancelled) setErr(e.message || String(e));
      }
    };
    refresh();
    const t = setInterval(refresh, 60_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  // Outbreak: alarm rows (flag set or P > 0.8), top 20 by alarm_prob desc.
  const outbreakAlerts = useMemo(() => {
    const rows = data?.preds['outbreak'] ?? [];
    return rows
      .filter(r => r.alarm_flag === 1 || (r.alarm_prob ?? 0) > 0.8)
      .sort((a, b) => (Number(b.alarm_prob) || 0) - (Number(a.alarm_prob) || 0))
      .slice(0, 20);
  }, [data]);

  // EARS endemic channel: alarm rows, top 20 by alarm_prob.
  const earsAlerts = useMemo(() => {
    const rows = data?.preds['ears'] ?? [];
    return rows
      .filter(r => r.alarm_flag === 1)
      .sort((a, b) => (Number(b.alarm_prob) || 0) - (Number(a.alarm_prob) || 0))
      .slice(0, 20);
  }, [data]);

  // Seasonal: newest forecast rows first (rows arrive year/week desc).
  const seasonalForecasts = useMemo(() => {
    const rows = data?.preds['seasonal'] ?? [];
    return rows.slice(0, 20);
  }, [data]);

  // Stockout: attrs.projected_days_remaining < 60, soonest first.
  const stockoutRisks = useMemo(() => {
    const rows = data?.preds['stockout_forecast'] ?? [];
    return rows
      .filter(r => Number(r.attrs?.projected_days_remaining ?? 999) < 60)
      .sort((a, b) => (Number(a.attrs?.projected_days_remaining) || 0) - (Number(b.attrs?.projected_days_remaining) || 0))
      .slice(0, 20);
  }, [data]);

  // KPI counts.
  const kpis = useMemo(() => {
    if (!data) return null;
    const k: Record<string, { total: number; alarms: number; lastRun: ml.MlRun | null }> = {};
    for (const m of data.models) {
      const rows = data.preds[m.name] ?? [];
      const total = rows.length;
      let alarms = 0;
      if (m.name === 'outbreak') alarms = outbreakAlerts.length;
      else if (m.name === 'ears') alarms = earsAlerts.length;
      else if (m.name === 'stockout_forecast') alarms = stockoutRisks.length;
      else if (m.name === 'seasonal') alarms = seasonalForecasts.length;
      else alarms = rows.filter(r => r.alarm_flag === 1).length;   // ewars_lite + future
      const lastRun = (data.runs[m.name] ?? []).find(r => r.status === 'success') ?? null;
      k[m.name] = { total, alarms, lastRun };
    }
    return k;
  }, [data, outbreakAlerts, earsAlerts, seasonalForecasts, stockoutRisks]);

  return (
    <section>
      <p className="muted" style={{ margin: '0 0 1.4rem' }}>
        Live output from Palladium's batch ML models: EARS endemic-channel alarms, Farrington-style outbreak detection, seasonal-profile forecasts, an EWARS-style negative-binomial model with climate drivers, and stockout forecasts. All are runner Jobs scheduled by the weekly Airflow DAG; predictions are persisted under <code>ops_ml.ml_predictions</code> (flat forecast schema) and surfaced here. Refreshes every minute.
      </p>

      {err && (
        <div className="card card-elev" style={{ borderLeft: '3px solid var(--red-500, #c0392b)', marginBottom: '1rem' }}>
          <strong>ML feed error:</strong> {err}
        </div>
      )}
      {!data && !err && <p className="muted">Loading models…</p>}

      {kpis && (
        <div className="grid grid-4">
          {data!.models.map(m => {
            const k = kpis[m.name];
            const sev = k.alarms > 0 ? (m.name === 'stockout_forecast' || m.name === 'outbreak' ? 'alert' : 'watch') : 'good';
            return (
              <div className="kpi" key={m.name}>
                <div className="kpi-lbl">{m.name} · @champion v{m.champion_version ?? '?'}</div>
                <div className="kpi-val"><span className={`pill ${sev}`}>{k.alarms}</span></div>
                <div className="kpi-delta">
                  {k.total.toLocaleString()} predictions · last run{' '}
                  {k.lastRun ? fmtAgo(k.lastRun.finished_at ?? k.lastRun.started_at) : 'never'}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="grid grid-2 gap-lg mt-lg">
        <div className="card card-elev">
          <div className="card-hdr">
            <h4>Outbreak alarms · last batch</h4>
            <span className="meta">Farrington-style · threshold breaches</span>
          </div>
          {outbreakAlerts.length === 0 ? (
            <p className="muted" style={{ margin: '1rem' }}>No outbreak flags in the latest run.</p>
          ) : (
            <table className="dash" style={{ margin: 0 }}>
              <thead><tr><th>Disease</th><th>District</th><th>Week</th><th>Baseline</th><th>Threshold</th><th>P(alarm)</th></tr></thead>
              <tbody>
                {outbreakAlerts.map((r, i) => (
                  <tr key={i}>
                    <td>{r.disease || 'n/a'}</td>
                    <td><code>{(r.district || 'n/a').slice(-12)}</code></td>
                    <td>{wk(r)}</td>
                    <td>{num(r.endemic_mean, 1)}</td>
                    <td>{num(r.alarm_threshold, 1)}</td>
                    <td><span className="pill alert">{pct(r.alarm_prob)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card card-elev">
          <div className="card-hdr">
            <h4>EARS endemic-channel alarms</h4>
            <span className="meta">same-week baseline · mean + 3·sd</span>
          </div>
          {earsAlerts.length === 0 ? (
            <p className="muted" style={{ margin: '1rem' }}>No endemic-channel alarms in the latest run.</p>
          ) : (
            <table className="dash" style={{ margin: 0 }}>
              <thead><tr><th>Disease</th><th>District</th><th>Week</th><th>z</th><th>Threshold</th><th>P(alarm)</th></tr></thead>
              <tbody>
                {earsAlerts.map((r, i) => (
                  <tr key={i}>
                    <td>{r.disease || 'n/a'}</td>
                    <td><code>{(r.district || 'n/a').slice(-12)}</code></td>
                    <td>{wk(r)}</td>
                    <td><span className="pill alert">{num(r.outbreak_z, 1)}</span></td>
                    <td>{num(r.alarm_threshold, 1)}</td>
                    <td>{pct(r.alarm_prob)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="grid grid-2 gap-lg mt-lg">
        <div className="card card-elev">
          <div className="card-hdr">
            <h4>Stockout forecast · at-risk commodities</h4>
            <span className="meta">days remaining &lt; 60 · Holt-Winters projection</span>
          </div>
          {stockoutRisks.length === 0 ? (
            <p className="muted" style={{ margin: '1rem' }}>No commodities projected to stock out within 60 days.</p>
          ) : (
            <table className="dash" style={{ margin: 0 }}>
              <thead><tr><th>Commodity</th><th>SOH</th><th>AMC 30d</th><th>Days left</th><th>Status</th></tr></thead>
              <tbody>
                {stockoutRisks.map((r, i) => {
                  const dr = Number(r.attrs?.projected_days_remaining);
                  const sev = dr < 14 ? 'alert' : dr < 30 ? 'watch' : 'muted';
                  return (
                    <tr key={i}>
                      <td>{r.district || 'n/a'}</td>
                      <td>{num(r.attrs?.soh_now, 0)}</td>
                      <td>{num(r.attrs?.amc_30d, 0)}</td>
                      <td><span className={`pill ${sev}`}>{num(dr, 0)} d</span></td>
                      <td>{r.attrs?.status ?? 'n/a'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        <div className="card card-elev">
          <div className="card-hdr">
            <h4>Seasonal forecasts · next periods</h4>
            <span className="meta">weekly seasonal profile · IDSR diseases</span>
          </div>
          {seasonalForecasts.length === 0 ? (
            <p className="muted" style={{ margin: '1rem' }}>No forecast rows in the latest run.</p>
          ) : (
            <table className="dash" style={{ margin: 0 }}>
              <thead><tr><th>Disease</th><th>District</th><th>Week</th><th>Predicted</th><th>95% range</th></tr></thead>
              <tbody>
                {seasonalForecasts.map((r, i) => (
                  <tr key={i}>
                    <td>{r.disease || 'n/a'}</td>
                    <td><code>{(r.district || 'n/a').slice(-12)}</code></td>
                    <td>{wk(r)}</td>
                    <td>{num(r.pred_cases_median ?? r.pred_cases_mean, 1)}</td>
                    <td className="muted">
                      {num(r.pred_cases_lwr95, 1)} to {num(r.pred_cases_upr95, 1)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <p className="muted" style={{ marginTop: '1.6rem', fontSize: '0.78rem' }}>
        Model registry lives in MLflow (in-cluster service <code>mlflow.nhdw.svc.cluster.local:5000</code>) ·
        retraining + @champion promotion happens in the admin dashboard
      </p>
    </section>
  );
}
