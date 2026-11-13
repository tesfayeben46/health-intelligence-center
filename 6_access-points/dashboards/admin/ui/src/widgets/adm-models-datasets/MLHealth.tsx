/**
 * Models (page admin/models-datasets, widget adm-models-datasets, in the Data
 * nav group beside Quality & Lineage): the central view of every model the
 * platform carries, submitted and accepted, including the ones graduated into
 * BI integration and the public
 * models living in workshop projects. One row per registered model with its
 * latest run, drift posture (PSI/KS from the monitoring job), latest
 * performance window and schedule staleness. Deep work on a model continues
 * in the workshop; the registry itself is one click away in MLflow.
 *
 * Data:
 *   GET /gw/ml/monitoring/fleet: the fleet summary (30s refresh)
 *   GET /gw/ml/{model}/drift?limit=200: per-feature drift drilldown
 *   GET /gw/ml/{model}/performance?limit=200: metric windows over time
 *
 * A 404 from the fleet endpoint (a gateway build without it) renders as an
 * explicit unavailable state. Every figure is real or empty.
 *
 * Table standard: the fleet table uses tableKit (fixed height + pagination
 * + search + sortable columns) while keeping the expandable detail rows.
 */
import { Fragment, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react';
import * as ml from '../../api/ml';
import ModelControls from './ModelControls';
import { useTableKit, TableToolbar, TablePager, SortTh } from '../../kit/tableKit';
import '../../styles/mlhealth.css';

// Operator-tool hosts follow the host this admin SPA is served on (admin.<base> -> <sub>.<base>),
// so the whole system re-homes on a single hostBase with no rebuild. No window (SSR) -> dev fallback.
const HOST_BASE = (typeof window !== 'undefined' ? window.location.host.replace(/^admin\./, '') : '127.0.0.1.nip.io');
const toolUrl = (sub: string, path = ''): string => `https://${sub}.${HOST_BASE}${path}`;


const WORKSHOP_MODELS_URL = toolUrl('workshop', '/');
// tool-gate-fronted registry: enter via the gate sign-in leg so the existing
// privileged-ops session admits silently and lands on the MLflow home; the gate
// enforces the role too (R-CONS-*, R-OPS-ADM, R-SUPER: a non-permitted role
// gets the gate's friendly 403, never a broken page).
const MLFLOW_URL = toolUrl('mlflow', '/oauth2gate/login?realm=privileged-ops&rd=/');

/* formatting (real values only; an em-dash placeholder when absent) */

function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return 'n/a';
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return 'n/a';
  if (ms < 60_000)     return `${Math.max(0, Math.round(ms / 1000))}s ago`;
  if (ms < 3_600_000)  return `${Math.round(ms / 60_000)}m ago`;
  if (ms < 86_400_000) return `${Math.round(ms / 3_600_000)}h ago`;
  return `${Math.round(ms / 86_400_000)}d ago`;
}
function fmtDay(iso: string | null | undefined): string {
  if (!iso) return 'n/a';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}
function fmtMetric(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return 'n/a';
  const a = Math.abs(v);
  if (a >= 100) return v.toFixed(1);
  if (a >= 10)  return v.toFixed(2);
  return v.toFixed(3);
}
function fmtPsi(v: number | null | undefined): string {
  return v == null || Number.isNaN(v) ? 'n/a' : v.toFixed(2);
}
function runPill(status: string | null): { cls: string; label: string } {
  if (status === 'success') return { cls: 'good',    label: 'success' };
  if (status === 'failed')  return { cls: 'alert',   label: 'failed' };
  if (status === 'running') return { cls: 'info',    label: 'running' };
  return { cls: 'neutral', label: status || 'no runs' };
}
function aliasPill(alias: string | null): string {
  if (alias === 'champion')   return 'good';
  if (alias === 'challenger') return 'info';
  return 'neutral';
}

/* performance-over-time line chart
 * Single series (one metric): no legend box; the heading names the
 * metric. 2px brand-ink line, recessive gridlines, min/max axis labels,
 * direct label on the last point, nearest-point hover tooltip. All text
 * wears ink tokens; the line wears the same navy as the DQ sparkline. */

type ChartPoint = { x: number; t: string | null; v: number; n: number | null; ver: string | null };

function PerfChart({ rows, metric }: { rows: ml.PerfRow[]; metric: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  const W = 640, H = 200;
  const PAD = { l: 46, r: 84, t: 16, b: 26 };

  const pts: ChartPoint[] = useMemo(() => {
    const usable = rows
      .filter(r => r.metric === metric && r.value != null && !Number.isNaN(Number(r.value)))
      .map(r => ({ t: r.window_end ?? r.computed_at, v: Number(r.value), n: r.sample_size, ver: r.model_version }))
      .sort((a, b) => String(a.t ?? '').localeCompare(String(b.t ?? '')));
    const iw = W - PAD.l - PAD.r;
    const step = usable.length > 1 ? iw / (usable.length - 1) : 0;
    return usable.map((p, i) => ({ ...p, x: PAD.l + i * step }));
  }, [rows, metric]);

  if (pts.length < 2) {
    return (
      <p className="mlh-chart-empty muted">
        {pts.length === 1
          ? `Only one ${metric} window recorded so far. A trend needs at least two.`
          : `No ${metric} windows recorded yet.`}
      </p>
    );
  }

  const vs = pts.map(p => p.v);
  const vMax = Math.max(...vs), vMin = Math.min(...vs);
  const range = (vMax - vMin) || Math.abs(vMax) || 1;
  const ih = H - PAD.t - PAD.b;
  const y = (v: number) => PAD.t + ih - ((v - vMin) / range) * ih;
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)},${y(p.v).toFixed(1)}`).join(' ');
  const last = pts[pts.length - 1];
  const grid = [0, 0.5, 1].map(f => PAD.t + ih * f);

  function onMove(e: ReactMouseEvent<SVGSVGElement>) {
    const el = svgRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const mx = ((e.clientX - rect.left) / rect.width) * W;
    let best = 0, bestDist = Infinity;
    pts.forEach((p, i) => {
      const dist = Math.abs(p.x - mx);
      if (dist < bestDist) { bestDist = dist; best = i; }
    });
    setHover(best);
  }

  const hp = hover != null ? pts[hover] : null;
  // Keep the tooltip inside the plot: flip to the left past the midpoint.
  const tipLeft = hp != null && hp.x > W * 0.55;

  return (
    <svg
      ref={svgRef}
      className="mlh-chart"
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={`${metric} over time, ${pts.length} windows, latest ${fmtMetric(last.v)}`}
      onMouseMove={onMove}
      onMouseLeave={() => setHover(null)}
    >
      {/* recessive gridlines + y extents */}
      {grid.map(gy => (
        <line key={gy} className="mlh-grid" x1={PAD.l} x2={W - PAD.r} y1={gy} y2={gy} />
      ))}
      <text className="mlh-axis" x={PAD.l - 6} y={y(vMax) + 3} textAnchor="end">{fmtMetric(vMax)}</text>
      <text className="mlh-axis" x={PAD.l - 6} y={y(vMin) + 3} textAnchor="end">{fmtMetric(vMin)}</text>
      {/* x extents */}
      <text className="mlh-axis" x={pts[0].x} y={H - 8} textAnchor="start">{fmtDay(pts[0].t)}</text>
      <text className="mlh-axis" x={last.x} y={H - 8} textAnchor="end">{fmtDay(last.t)}</text>

      {/* the series */}
      <path className="mlh-line" d={d} />
      {/* direct label on the latest point (selective, not every point) */}
      <circle className="mlh-dot" cx={last.x} cy={y(last.v)} r={3} />
      <text className="mlh-last" x={last.x + 8} y={y(last.v) + 4}>
        {fmtMetric(last.v)}
      </text>

      {/* hover: nearest-point marker + tooltip */}
      {hp && (
        <g>
          <line className="mlh-cross" x1={hp.x} x2={hp.x} y1={PAD.t} y2={PAD.t + ih} />
          <circle className="mlh-dot-hover" cx={hp.x} cy={y(hp.v)} r={4.5} />
          <g transform={`translate(${tipLeft ? hp.x - 158 : hp.x + 10}, ${PAD.t})`}>
            <rect className="mlh-tip-bg" width={148} height={44} rx={6} />
            <text className="mlh-tip-strong" x={9} y={17}>{metric} {fmtMetric(hp.v)}</text>
            <text className="mlh-tip" x={9} y={33}>
              {fmtDay(hp.t)}{hp.n != null ? ` · n=${hp.n.toLocaleString()}` : ''}{hp.ver ? ` · v${hp.ver}` : ''}
            </text>
          </g>
        </g>
      )}
    </svg>
  );
}

/* per-model drilldown (expandable row body) */

type DetailState = {
  loading: boolean;
  err: string | null;
  drift: ml.DriftRow[] | null;
  perf: ml.PerfRow[] | null;
};

function ModelDetail({ item, det }: { item: ml.FleetItem; det: DetailState }) {
  if (det.loading) return <p className="muted mlh-det-note">Loading drift &amp; performance history…</p>;
  if (det.err)     return (
    <div className="mlh-note mlh-note-alert">
      <svg className="ico ico-sm"><use href="#i-alert" /></svg>
      Drilldown failed: {det.err}
    </div>
  );

  const drift = det.drift ?? [];
  const perf  = det.perf ?? [];
  // Chart the newest window's metric (one series, one axis); the fleet's
  // latest_metric wins when present so the row and the chart agree.
  const metric = item.latest_metric
    ?? [...perf].sort((a, b) =>
         String(b.computed_at ?? '').localeCompare(String(a.computed_at ?? '')))[0]?.metric
    ?? null;

  return (
    <div className="mlh-det">
      <div className="mlh-det-col">
        <h5 className="mlh-det-h">Feature drift · latest measurements</h5>
        {drift.length === 0 ? (
          <p className="muted mlh-det-note">No drift measurements recorded for this model yet.</p>
        ) : (
          <div className="mlh-table-scroll">
            <table className="dash mlh-drift-table">
              <thead>
                <tr>
                  <th>Feature</th><th>Kind</th>
                  <th className="num">PSI</th><th className="num">threshold</th>
                  <th className="num">KS</th><th>Status</th><th>Computed</th>
                </tr>
              </thead>
              <tbody>
                {drift.map((r, i) => (
                  <tr key={`${r.feature}-${r.computed_at ?? i}`} className={r.drifted ? 'is-drifted' : ''}>
                    <td>
                      <code>{r.feature}</code>
                      {r.note && <div className="mlh-drift-note muted">{r.note}</div>}
                    </td>
                    <td className="muted">{r.kind}</td>
                    <td className="num">{fmtPsi(r.psi)}</td>
                    <td className="num muted">{fmtPsi(r.psi_threshold)}</td>
                    <td className="num">{fmtPsi(r.ks)}</td>
                    <td><span className={`pill ${r.drifted ? 'alert' : 'good'}`}>{r.drifted ? 'drifted' : 'ok'}</span></td>
                    <td className="muted">{fmtAgo(r.computed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="mlh-det-col">
        <h5 className="mlh-det-h">
          Performance over time{metric ? <> · <code>{metric}</code></> : ''}
        </h5>
        {metric
          ? <PerfChart rows={perf} metric={metric} />
          : <p className="muted mlh-det-note">No performance windows recorded for this model yet.</p>}
        {item.perf_computed_at && (
          <p className="mlh-det-foot muted">latest window computed {fmtAgo(item.perf_computed_at)}</p>
        )}
      </div>
    </div>
  );
}

/* the page */

export default function MLHealth() {
  const [items, setItems] = useState<ml.FleetItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [notDeployed, setNotDeployed] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, DetailState>>({});

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const { items: rows } = await ml.getFleet();
        if (cancelled) return;
        setItems(rows ?? []);
        setErr(null);
        setNotDeployed(false);
      } catch (e: any) {
        if (cancelled) return;
        if (e instanceof ml.MlHttpError && e.status === 404) {
          setNotDeployed(true);
          setErr(null);
        } else {
          setErr(e?.message || String(e));
        }
      }
    };
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  async function toggle(model: string) {
    if (expanded === model) { setExpanded(null); return; }
    setExpanded(model);
    if (details[model] && !details[model].err) return;   // already loaded
    setDetails(d => ({ ...d, [model]: { loading: true, err: null, drift: null, perf: null } }));
    const [dr, pf] = await Promise.allSettled([
      ml.getDrift(model, 200), ml.getPerformance(model, 200),
    ]);
    setDetails(d => ({
      ...d,
      [model]: {
        loading: false,
        // A 404 drilldown means nothing is recorded yet: show empty, not an error.
        err: [dr, pf].some(s => s.status === 'rejected'
                                && !((s.reason instanceof ml.MlHttpError) && s.reason.status === 404))
          ? [dr, pf].filter(s => s.status === 'rejected').map((s: any) => String(s.reason?.message || s.reason)).join(' · ')
          : null,
        drift: dr.status === 'fulfilled' ? (dr.value.items ?? []) : [],
        perf:  pf.status === 'fulfilled' ? (pf.value.items ?? []) : [],
      },
    }));
  }

  const kpis = useMemo(() => {
    if (!items) return null;
    return {
      total:    items.length,
      drifting: items.filter(i => (i.drifted_features ?? 0) > 0).length,
      failed:   items.filter(i => i.latest_run_status === 'failed').length,
      stale:    items.filter(i => i.stale).length,
    };
  }, [items]);

  // Table standard: fixed height + pagination + search + sortable columns.
  const kit = useTableKit(items, {
    pageSize: 15,
    search: it => `${it.model_name} ${it.alias ?? ''} ${it.latest_run_status ?? ''} ${it.latest_metric ?? ''}`,
    sortVals: {
      model: it => it.model_name,
      run:   it => it.latest_run_at,
      drift: it => it.max_psi,
      perf:  it => it.latest_metric_value,
      stale: it => (it.stale ? 1 : 0),
    },
    initialSort: { key: 'model', dir: 'asc' },
  });

  return (
    <section className="mlh-page">
      <p className="page-lede">
        Every model the platform carries, in one place: submitted and accepted models,
        including those graduated into BI integration and the public models living in
        workshop projects, with latest run, feature drift (PSI/KS from the monitoring
        job), newest performance window, and schedule staleness. Investigate and retrain
        in the workshop; the registry of record is MLflow.
      </p>

      {/* prominent registry deep link: always visible, even in degraded states */}
      <div className="mlh-linkbar">
        <a className="btn btn-primary btn-sm" href={MLFLOW_URL} target="_blank" rel="noreferrer"
           title="Model registry, experiment tracking and run artifacts">
          <svg className="ico ico-sm"><use href="#i-microscope" /></svg>
          Open MLflow registry <svg className="ico ico-sm"><use href="#i-external" /></svg>
        </a>
        <a className="btn btn-ghost btn-sm" href={WORKSHOP_MODELS_URL} target="_blank" rel="noreferrer">
          Workshop model catalogue <svg className="ico ico-sm"><use href="#i-external" /></svg>
        </a>
      </div>

      {/* Summary KPIs */}
      {kpis && (
        <div className="grid grid-4 mlh-kpis">
          <div className="kpi kpi-info">
            <div className="kpi-lbl">Models monitored</div>
            <div className="kpi-val">{kpis.total}</div>
            <div className="kpi-delta">registered in MLflow</div>
          </div>
          <div className={`kpi kpi-${kpis.drifting > 0 ? 'alert' : 'good'}`}>
            <div className="kpi-lbl">Drifting now</div>
            <div className="kpi-val">{kpis.drifting}</div>
            <div className="kpi-delta">models with at least 1 drifted feature</div>
          </div>
          <div className={`kpi kpi-${kpis.failed > 0 ? 'alert' : 'good'}`}>
            <div className="kpi-lbl">Failed last runs</div>
            <div className="kpi-val">{kpis.failed}</div>
            <div className="kpi-delta">latest run ended in failure</div>
          </div>
          <div className={`kpi kpi-${kpis.stale > 0 ? 'watch' : 'good'}`}>
            <div className="kpi-lbl">Stale schedules</div>
            <div className="kpi-val">{kpis.stale}</div>
            <div className="kpi-delta">overdue vs. their cadence</div>
          </div>
        </div>
      )}

      {/* explicit degraded states */}
      {notDeployed && (
        <div className="card card-elev mlh-offline">
          <svg className="ico"><use href="#i-info" /></svg>
          <div>
            <h4>Fleet monitoring endpoint not available yet</h4>
            <p>The gateway answered 404 for <code>/gw/ml/monitoring/fleet</code>. It
            hasn't been rebuilt with the monitoring routes yet. No monitoring data
            can be shown until it is; nothing here is simulated.</p>
          </div>
        </div>
      )}
      {err && (
        <div className="mlh-note mlh-note-alert">
          <svg className="ico ico-sm"><use href="#i-alert" /></svg>
          Fleet feed error: {err}
        </div>
      )}
      {!items && !err && !notDeployed && <p className="muted mlh-loading">Loading model fleet…</p>}

      {/* Fleet table */}
      {items && (
        <div className="page-section">
          <div className="ps-hdr">
            <div>
              <h3 className="ps-title"><svg className="ico"><use href="#i-pulse" /></svg> Model fleet</h3>
              <p className="ps-sub">One row per model. Expand a row for per-feature drift and the performance trend.</p>
            </div>
            <div className="ps-meta">drift &amp; performance · <strong>monitoring job</strong> · refreshes every 30s</div>
          </div>

          <div className="card card-elev mlh-panel">
            <TableToolbar kit={kit} placeholder="Search model, alias, status, metric…" unit="models" />
            <div className="tk-scroll">
              <table className="dash mlh-fleet">
                <thead>
                  <tr>
                    <SortTh kit={kit} k="model">Model</SortTh>
                    <SortTh kit={kit} k="run">Last run</SortTh>
                    <SortTh kit={kit} k="drift">Drift</SortTh>
                    <SortTh kit={kit} k="perf">Performance</SortTh>
                    <SortTh kit={kit} k="stale">Schedule</SortTh>
                    <th className="mlh-th-actions"></th>
                  </tr>
                </thead>
                <tbody>
                  {kit.pageRows.map(it => {
                    const run = runPill(it.latest_run_status);
                    const drifted = it.drifted_features ?? 0;
                    const checked = it.features_checked ?? 0;
                    const isOpen = expanded === it.model_name;
                    return (
                      <Fragment key={it.model_name}>
                        <tr className={isOpen ? 'is-open' : ''}>
                          <td>
                            <div className="mlh-model">
                              <span className="mlh-model-name">{it.model_name}</span>
                              {it.alias && <span className={`pill ${aliasPill(it.alias)}`}>@{it.alias}</span>}
                            </div>
                          </td>
                          <td>
                            <span className={`pill ${run.cls}`}>{run.label}</span>
                            {it.latest_run_at && <span className="mlh-when muted"> {fmtAgo(it.latest_run_at)}</span>}
                          </td>
                          <td>
                            {checked > 0 ? (
                              <span className={`mlh-drift ${drifted > 0 ? 'is-alert' : ''}`}>
                                {drifted}/{checked} features drifted
                                {it.max_psi != null && <span className="mlh-psi"> · max PSI {fmtPsi(it.max_psi)}</span>}
                              </span>
                            ) : (
                              <span className="muted">no drift signal yet</span>
                            )}
                          </td>
                          <td>
                            {it.latest_metric ? (
                              <>
                                <span className="mlh-metric"><code>{it.latest_metric}</code> {fmtMetric(it.latest_metric_value)}</span>
                                {it.perf_computed_at && <span className="mlh-when muted"> · {fmtAgo(it.perf_computed_at)}</span>}
                              </>
                            ) : (
                              <span className="muted">no performance window yet</span>
                            )}
                          </td>
                          <td>
                            {it.stale
                              ? <span className="pill watch">stale</span>
                              : <span className="pill good">on schedule</span>}
                          </td>
                          <td className="mlh-actions">
                            <button className="btn btn-sm btn-ghost" onClick={() => toggle(it.model_name)}>
                              {isOpen ? 'Hide' : 'Details'}{' '}
                              <svg className="ico ico-sm"><use href={isOpen ? '#i-chevron-down' : '#i-chevron-right'} /></svg>
                            </button>
                            <a
                              className="btn btn-sm btn-ghost mlh-workshop-link"
                              href={WORKSHOP_MODELS_URL}
                              target="_blank" rel="noreferrer"
                              title="Open the model catalogue in the workshop"
                            >
                              Open in workshop <svg className="ico ico-sm"><use href="#i-external" /></svg>
                            </a>
                          </td>
                        </tr>
                        {isOpen && (
                          <tr className="mlh-det-row">
                            <td colSpan={6}>
                              <ModelDetail
                                item={it}
                                det={details[it.model_name] ?? { loading: true, err: null, drift: null, perf: null }}
                              />
                              <ModelControls model={it.model_name} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                  {items.length === 0 && (
                    <tr>
                      <td colSpan={6} className="muted">
                        The monitoring feed is live but reports no models yet. Register a
                        model in MLflow and run the monitoring job to populate this table.
                      </td>
                    </tr>
                  )}
                  {items.length > 0 && kit.filteredTotal === 0 && (
                    <tr><td colSpan={6} className="muted">No models match the search.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
            <TablePager kit={kit} />
          </div>
        </div>
      )}
    </section>
  );
}
