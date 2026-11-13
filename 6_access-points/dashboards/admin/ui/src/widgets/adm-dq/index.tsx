/**
 * Data Quality & lineage: the operator's headline view of pipeline health.
 *
 * Everything here reads live from /gw/dq/* (which fronts the dbt manifest +
 * run_results artifacts, the raw-lake _manifest.json sidecars, and the
 * bi_quality / business-vault marts via Trino). A widget that cannot reach its
 * data shows a "not yet available" note rather than numbers. Auto-refreshes
 * every 30s.
 *
 * Layout (top to bottom): headline KPIs, one merged per-source health widget
 * (ingestion freshness + data quality, folded together per source), quality
 * dimensions & cross-source reconciliation, then dbt tests (with acknowledge).
 * compact lineage summary + operator tool deep-links. The full interactive
 * DAG lives in dbt Docs: this page is the summary, not the graph.
 */
import { useEffect, useMemo, useState } from 'react';
import * as dq from '../../api/dq';
import { useTableKit, TableToolbar, TablePager, SortTh } from '../../kit/tableKit';
import '../../styles/dq.css';

// Operator-tool hosts follow the host this admin SPA is served on (admin.<base> -> <sub>.<base>),
// so the whole system re-homes on a single hostBase with no rebuild. No window (SSR) -> dev fallback.
const HOST_BASE = (typeof window !== 'undefined' ? window.location.host.replace(/^admin\./, '') : '127.0.0.1.nip.io');
const toolUrl = (sub: string, path = ''): string => `https://${sub}.${HOST_BASE}${path}`;


// dbt-manifest lineage tiers. NB: the "source" tier counts dbt source()
// TABLE declarations (one per raw table we read), not the six source
// SYSTEMS: hence the "Source tables" label, so ~29 is never mistaken for
// "29 source systems".
const TIER_LABELS: Record<dq.LineageNode['tier'], string> = {
  source:          'Source tables',
  staging:         'Staging',
  vault_raw:       'Vault · raw',
  vault_business:  'Vault · business',
  mart:            'Marts',
  other:           'Other',
};

// Canonical six ingested sources + a one-line "what it is". Keys match the
// gateway SOURCES registry and (after its alias normalisation) the source
// label from mart_data_quality, so freshness + quality fold onto one row.
const SOURCE_META: Record<string, string> = {
  'DHIS2':    'HMIS aggregate',
  'EthioEMR': 'FHIR encounters',
  'HCMIS':    'Supply chain',
  'mBrana':   'USSD stock logs',
  'MFR':      'Facility registry',
  'Climate':  'Weekly climate feed',
};

const TOOLS = [
  // Real product marks, same files the Documentation page tiles use
  // (/static/assets/brand/: local, nothing fetched at runtime).
  { label: 'dbt Docs', sub: 'Full interactive lineage DAG',
    href: toolUrl('dbt-docs', '/'), brand: '/static/assets/brand/dbt.svg' },
  // Lake console = the SeaweedFS filer's own file browser behind seaweedfs-gate
  // (SSO, admin roles only: the UI is write-capable).
  { label: 'Lake browser', sub: 'Object store console (SeaweedFS)',
    href: toolUrl('seaweedfs', '/'), brand: '/static/assets/brand/seaweedfs.svg' },
  { label: 'Airflow', sub: 'Ingestion + build run history',
    href: toolUrl('airflow', '/'), brand: '/static/assets/brand/airflow.svg' },
];

const DIM_ORDER: Array<{ key: keyof dq.Dimensions['dimensions']; label: string; hint: string }> = [
  { key: 'completeness', label: 'Completeness', hint: 'expected records arrived' },
  { key: 'validity',     label: 'Validity',     hint: 'values in valid range' },
  { key: 'timeliness',   label: 'Timeliness',   hint: 'within reporting deadline' },
  { key: 'consistency',  label: 'Consistency',  hint: 'relationship tests pass' },
  { key: 'uniqueness',   label: 'Uniqueness',   hint: 'no duplicate keys' },
];

const STATUS_TONE: Record<string, 'good' | 'watch' | 'alert' | 'neutral'> = {
  live: 'good', stale: 'watch', down: 'alert', unknown: 'neutral',
};

// formatting helpers
function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return 'n/a';
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return 'n/a';
  if (ms < 60_000)     return `${Math.max(0, Math.round(ms / 1000))}s ago`;
  if (ms < 3_600_000)  return `${Math.round(ms / 60_000)}m ago`;
  if (ms < 86_400_000) return `${Math.round(ms / 3_600_000)}h ago`;
  return `${Math.round(ms / 86_400_000)}d ago`;
}
function fmtLag(h: number | null): string {
  if (h == null) return 'n/a';
  if (h < 1)  return `${Math.round(h * 60)}m`;
  if (h < 48) return `${h.toFixed(1)}h`;
  return `${(h / 24).toFixed(1)}d`;
}
function fmtPct(n: number | null | undefined): string {
  return n == null ? 'n/a' : `${Number(n).toFixed(n % 1 === 0 ? 0 : 1)}%`;
}
function fmtNum(n: number | null | undefined): string {
  return n == null ? 'n/a' : Number(n).toLocaleString();
}
function pctTone(n: number | null | undefined, good = 90, watch = 75): 'good' | 'watch' | 'alert' | 'flat' {
  if (n == null) return 'flat';
  if (n >= good)  return 'good';
  if (n >= watch) return 'watch';
  return 'alert';
}
function shortTestId(uid: string): { model: string; test: string } {
  const parts = uid.split('.');
  if (parts.length < 3) return { model: 'n/a', test: uid };
  const body = parts[2];
  const m = body.match(/^(.+?)__(.+)$/);
  if (m) return { test: m[1], model: m[2] };
  return { test: body, model: parts[1] };
}

// tiny inline sparkline
function Sparkline({ points }: { points: number[] }) {
  const w = 92, h = 24;
  if (!points || points.length < 2) return <span className="dq-spark-empty">no history</span>;
  const max = Math.max(...points), min = Math.min(...points);
  const range = (max - min) || 1;
  const step = w / (points.length - 1);
  const y = (p: number) => h - 2 - ((p - min) / range) * (h - 4);
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${(i * step).toFixed(1)},${y(p).toFixed(1)}`).join(' ');
  const last = points[points.length - 1], prev = points[points.length - 2];
  const tone = last < prev * 0.75 ? 'down' : last > prev * 1.25 ? 'up' : '';
  return (
    <svg className={`dq-spark ${tone}`} width={w} height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <path d={d} />
      <circle cx={(w).toFixed(1)} cy={y(last).toFixed(1)} r={2.2} />
    </svg>
  );
}

function Meter({ pct, tone }: { pct: number | null | undefined; tone?: string }) {
  const v = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  return (
    <div className="dq-meter" aria-hidden>
      <span className={`dq-meter-fill ${tone || ''}`} style={{ width: `${v}%` }} />
    </div>
  );
}

// One quality cell: labelled meter + tabular %. Muted when the source has no
// reliable signal for this dimension yet.
function QCell({ pct }: { pct: number | null | undefined }) {
  const tone = pctTone(pct);
  return (
    <div className="dq-q-cell">
      <Meter pct={pct} tone={tone} />
      <span className={`dq-q-pct ${pct == null ? 'is-na' : tone}`}>{fmtPct(pct)}</span>
    </div>
  );
}

function DQ() {
  const [summary,    setSummary]    = useState<dq.DqSummary | null>(null);
  const [fails,      setFails]      = useState<dq.DqTest[]>([]);
  const [warns,      setWarns]      = useState<dq.DqTest[]>([]);
  const [lineage,    setLineage]    = useState<dq.DqLineage | null>(null);
  const [sources,    setSources]    = useState<dq.SourcesResp | null>(null);
  const [ingestion,  setIngestion]  = useState<dq.IngestionResp | null>(null);
  const [dims,       setDims]       = useState<dq.Dimensions | null>(null);
  const [recon,      setRecon]      = useState<dq.ReconResp | null>(null);
  const [compliance, setCompliance] = useState<dq.Compliance | null>(null);
  const [flagsResp,  setFlagsResp]  = useState<dq.FlagsResp | null>(null);
  const [kpi,        setKpi]        = useState<dq.DqKpi | null>(null);
  const [detectBusy, setDetectBusy] = useState(false);
  const [detectMsg,  setDetectMsg]  = useState<string | null>(null);
  const [errs,       setErrs]       = useState<string[]>([]);

  const [ackBusy,  setAckBusy]  = useState<Record<string, boolean>>({});
  const [ackLocal, setAckLocal] = useState<Record<string, { by: string; at: string | null }>>({});
  const [ackErr,   setAckErr]   = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      const settled = await Promise.allSettled([
        dq.getSummary(), dq.getTests('fail', 25), dq.getTests('warn', 25),
        dq.getLineage(), dq.getSources(), dq.getIngestion(),
        dq.getDimensions(), dq.getReconciliation(), dq.getCompliance(),
        dq.getFlags('open', 50, 0), dq.getKpi(),
      ]);
      if (cancelled) return;
      const nextErrs: string[] = [];
      const [s, f, w, l, src, ing, dm, rc, cp, fl, kp] = settled;
      if (fl.status === 'fulfilled') setFlagsResp(fl.value); // absent pre-38 clusters degrade to the note
      if (kp.status === 'fulfilled') setKpi(kp.value);
      if (s.status  === 'fulfilled') setSummary(s.value);       else nextErrs.push(`summary: ${s.reason}`);
      if (f.status  === 'fulfilled') setFails(f.value);
      if (w.status  === 'fulfilled') setWarns(w.value);
      if (l.status  === 'fulfilled') setLineage(l.value);
      if (src.status === 'fulfilled') setSources(src.value);    else nextErrs.push(`sources: ${src.reason}`);
      if (ing.status === 'fulfilled') setIngestion(ing.value);
      if (dm.status  === 'fulfilled') setDims(dm.value);
      if (rc.status  === 'fulfilled') setRecon(rc.value);
      if (cp.status  === 'fulfilled') setCompliance(cp.value);
      setErrs(nextErrs);
    };
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  async function doAck(t: dq.DqTest) {
    setAckErr(null);
    setAckBusy(b => ({ ...b, [t.unique_id]: true }));
    try {
      const { test, model } = shortTestId(t.unique_id);
      const res = await dq.ackTest({
        unique_id: t.unique_id, model, test,
        message: t.message ?? undefined, severity: t.status,
      });
      setAckLocal(a => ({ ...a, [t.unique_id]: { by: res.acked_by, at: res.acked_at } }));
    } catch (e: any) {
      setAckErr(e?.message || String(e));
    } finally {
      setAckBusy(b => ({ ...b, [t.unique_id]: false }));
    }
  }

  // derived
  const failingTests = useMemo(() => [...fails, ...warns], [fails, warns]);

  // one merged per-source row: ingestion freshness (raw-lake manifests) +
  // data quality (mart_data_quality), joined on the canonical source key.
  const mergedSources = useMemo(() => {
    const qByName  = new Map((sources?.sources ?? []).map(s => [s.source, s]));
    const ingRows  = ingestion?.sources ?? [];
    const ingByName = new Map(ingRows.map(s => [s.source, s]));
    const order = ingRows.length
      ? ingRows.map(s => s.source)
      : (sources?.expected ?? Object.keys(SOURCE_META));
    return order.map(name => ({
      name,
      blurb: SOURCE_META[name] ?? '',
      ing: ingByName.get(name) ?? null,
      q:   qByName.get(name) ?? null,
    }));
  }, [sources, ingestion]);

  const tierChips = useMemo(() => {
    if (!lineage) return [];
    const counts = new Map<string, number>();
    for (const n of lineage.nodes) counts.set(n.tier, (counts.get(n.tier) ?? 0) + 1);
    return (lineage.tier_order ?? []).map(t => ({ tier: t, n: counts.get(t) ?? 0 }))
      .filter(c => c.n > 0);
  }, [lineage]);

  const modelTestRollup = useMemo(() => {
    const r = { pass: 0, warn: 0, fail: 0, untested: 0 };
    for (const n of lineage?.nodes ?? []) {
      if (n.tier === 'source') continue;
      if (n.test_status === 'fail') r.fail++;
      else if (n.test_status === 'warn') r.warn++;
      else if (n.test_status === 'pass') r.pass++;
      else r.untested++;
    }
    return r;
  }, [lineage]);

  const dbtTone: 'good' | 'watch' | 'alert' =
    !summary ? 'watch' : summary.fail > 0 ? 'alert' : summary.warn > 0 ? 'watch' : 'good';

  const liveCount  = (ingestion?.sources ?? []).filter(s => s.status === 'live').length;
  const ingestable = (ingestion?.sources ?? []).filter(s => s.ingested).length;

  const kanon = compliance?.k_anon;
  const kanonTone: 'good' | 'watch' | 'alert' =
    !kanon ? 'watch' : kanon.passed ? 'good' : kanon.present ? 'alert' : 'watch';

  return (
    <section className="dq-page">
      <p className="page-lede">
        Live pipeline-health headline for every ingested source: dbt build health, per-source
        freshness &amp; quality in one view, cross-source reconciliation, and lineage, read straight
        from each run's artifacts and the marts. The full interactive DAG lives in dbt Docs.
      </p>

      {/* Headline KPIs */}
      <div className="grid grid-4 dq-kpis">
        <div className={`kpi kpi-${dbtTone}`}>
          <div className="kpi-lbl">dbt build health</div>
          <div className="kpi-val">{summary ? `${summary.pass}/${summary.total}` : 'n/a'}</div>
          <div className="kpi-delta">{summary
            ? <>{summary.pass} pass · {summary.warn} warn · <strong>{summary.fail} fail</strong></>
            : 'loading…'}</div>
        </div>
        {/* runtime_s is real but comes from the last uploaded run_results:
            an artifact older than a day is flagged stale so a frozen number
            can't masquerade as a fresh build. */}
        <div className={`kpi kpi-${summary?.generated_at && (Date.now() - new Date(summary.generated_at).getTime()) > 24 * 3600e3 ? 'watch' : 'info'}`}>
          <div className="kpi-lbl">Last build</div>
          <div className="kpi-val">{summary ? `${summary.runtime_s.toFixed(1)}s` : 'n/a'}</div>
          <div className="kpi-delta">{summary?.generated_at
            ? `ran ${fmtAgo(summary.generated_at)}${(Date.now() - new Date(summary.generated_at).getTime()) > 24 * 3600e3 ? ' · artifact stale' : ''}`
            : ''}</div>
        </div>
        <div className={`kpi kpi-${ingestable && liveCount === ingestable ? 'good' : 'watch'}`}>
          <div className="kpi-lbl">Sources reporting on time</div>
          <div className="kpi-val">{ingestion ? `${liveCount} / ${ingestable || 'n/a'}` : 'n/a'}</div>
          <div className="kpi-delta">{ingestion ? 'within SLA · last ingest cycle' : 'scanning raw lake…'}</div>
        </div>
        <div className={`kpi kpi-${kanonTone}`}>
          <div className="kpi-lbl">k-anonymity · public marts</div>
          <div className="kpi-val">{kanon ? (kanon.passed ? 'Pass' : kanon.present ? 'Fail' : 'n/a') : 'n/a'}</div>
          <div className="kpi-delta">{kanon
            ? `${kanon.public_mart_count} bi_public marts · k >= 5`
            : 'checking…'}</div>
        </div>
      </div>

      {errs.length > 0 && (
        <div className="dq-note dq-note-warn">
          <svg className="ico ico-sm"><use href="#i-info" /></svg>
          Some feeds are still warming up: {errs.join(' · ')}
        </div>
      )}

      {/* Source health (merged freshness + quality) */}
      <div className="page-section">
        <div className="ps-hdr">
          <div>
            <h3 className="ps-title"><svg className="ico"><use href="#i-pulse" /></svg> Source health</h3>
            <p className="ps-sub">
              One row per ingested system: ingestion freshness and data quality together.
            </p>
          </div>
          <div className="ps-meta">
            freshness · <strong>raw-lake manifests</strong> &nbsp;·&nbsp; quality · <strong>mart_data_quality</strong>
          </div>
        </div>

        <div className="card card-elev dq-src-panel">
          <div className="dq-table-scroll">
            <table className="dash dq-src-table">
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Status</th>
                  <th>Last ingest</th>
                  <th className="num">Records</th>
                  <th className="num">Lag / SLA</th>
                  <th>Completeness</th>
                  <th>Validity</th>
                  <th>Volume trend</th>
                </tr>
              </thead>
              <tbody>
                {mergedSources.map(({ name, blurb, ing, q }) => {
                  const status  = ing?.status ?? 'unknown';
                  const tone    = STATUS_TONE[status] ?? 'neutral';
                  const overSla = ing?.lag_hours != null && ing.lag_hours > ing.sla_hours;
                  return (
                    <tr key={name}>
                      <td>
                        <div className="dq-src-name">{name}</div>
                        <div className="dq-src-blurb">{blurb}</div>
                      </td>
                      <td>
                        <span className={`pill dq-status ${tone}`}>{status}</span>
                      </td>
                      <td>
                        {ing?.ingested
                          ? <>
                              <div>{fmtAgo(ing.last_ingest)}</div>
                              {ing.changed_last_run == null
                                ? null
                                : ing.changed_last_run
                                  ? <span className="dq-chip dq-chip-changed">new data</span>
                                  : <span className="dq-chip">unchanged</span>}
                            </>
                          : <span className="muted">{ing?.note ?? 'no ingest yet'}</span>}
                      </td>
                      <td className="num">{fmtNum(ing?.records_last_cycle)}</td>
                      <td className={`num ${overSla ? 'dq-over' : ''}`}>
                        {fmtLag(ing?.lag_hours ?? null)}
                        <span className="muted"> / {fmtLag(ing?.sla_hours ?? null)}</span>
                      </td>
                      <td className="dq-q"><QCell pct={q?.completeness} /></td>
                      <td className="dq-q"><QCell pct={q?.validity} /></td>
                      <td>{ing ? <Sparkline points={ing.trend.map(p => p.rows)} /> : <span className="muted">n/a</span>}</td>
                    </tr>
                  );
                })}
                {mergedSources.length === 0 && (
                  <tr><td colSpan={8} className="muted">Scanning nhdw-raw manifests &amp; mart_data_quality…</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="dq-src-foot-notes">
            {sources?.note && <span className="muted"><svg className="ico ico-sm"><use href="#i-info" /></svg> Quality: {sources.note}</span>}
            {ingestion?.note && <span className="muted"><svg className="ico ico-sm"><use href="#i-info" /></svg> Freshness: {ingestion.note}</span>}
            {!sources?.note && !ingestion?.note && (
              <span className="muted">Blank quality cells mean the source carries no reliable signal for that dimension yet, never a fabricated score.</span>
            )}
          </div>
        </div>
      </div>

      {/* Quality dimensions & reconciliation */}
      {/* Compact band: dense inline-stat rows. All CSS is scoped to this
          widget (dqc-* / .dq-qr-compact) so it never touches the shared
          globals. */}
      <div className="page-section">
        <style>{`
          .dq-qr-compact { margin-top: 0.2rem; align-items: stretch; }
          .dq-qr-compact > .card { display: flex; flex-direction: column; padding: 0.85rem 1rem 0.95rem; }
          .dq-qr-compact .card-hdr { margin-bottom: 0.45rem; }
          .dq-qr-compact .card-hdr h4 { font-size: 0.9rem; }
          .dqc-list { display: flex; flex-direction: column; }
          .dqc-dim { display: grid; grid-template-columns: 7.25rem 1fr 3rem; align-items: center; gap: 0.7rem; padding: 0.42rem 0; }
          .dqc-dim + .dqc-dim, .dqc-row + .dqc-row { border-top: 1px solid var(--border); }
          .dqc-dim-name { font-size: 0.8rem; font-weight: 600; color: var(--ink-2); white-space: nowrap; }
          .dqc-dim .dq-meter, .dqc-row .dq-meter { height: 5px; }
          .dqc-dim-pct { text-align: right; font-size: 0.92rem; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--ink-3); }
          .dqc-dim.good  .dqc-dim-pct { color: var(--good); }
          .dqc-dim.watch .dqc-dim-pct { color: var(--watch); }
          .dqc-dim.alert .dqc-dim-pct { color: var(--alert); }
          .dqc-row { padding: 0.5rem 0; }
          .dqc-row-head { display: flex; justify-content: space-between; align-items: baseline; gap: 0.6rem; }
          .dqc-row-name { font-size: 0.82rem; font-weight: 700; color: var(--ink); }
          .dqc-row-sys { font-size: 0.68rem; color: var(--ink-4); }
          .dqc-row-pct { font-size: 1rem; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1; color: var(--ink-3); }
          .dqc-row-pct.good  { color: var(--good); }
          .dqc-row-pct.watch { color: var(--watch); }
          .dqc-row-pct.alert { color: var(--alert); }
          .dqc-row .dq-meter { margin: 0.32rem 0 0.3rem; }
          .dqc-row-foot { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 0.35rem 0.6rem; font-size: 0.7rem; color: var(--ink-3); }
          .dqc-chips { display: flex; flex-wrap: wrap; gap: 0.3rem; }
          .dqc-chip { display: inline-flex; padding: 0.08rem 0.42rem; border-radius: 999px; font-size: 0.64rem; font-weight: 600; background: var(--bg-2); color: var(--ink-3); white-space: nowrap; }
          .dqc-empty { margin: 0.3rem 0; font-size: 0.8rem; color: var(--ink-4); }
        `}</style>
        <div className="ps-hdr">
          <div>
            <h3 className="ps-title"><svg className="ico"><use href="#i-shield-check" /></svg> Quality &amp; reconciliation</h3>
            <p className="ps-sub">Warehouse-wide dimension rollup and real cross-source master-data agreement.</p>
          </div>
        </div>

        <div className="grid grid-2 dq-qr-compact" style={{ gap: '1rem' }}>
          {/* Dimension scorecard: dense inline rows (name · meter · %) */}
          <div className="card card-elev">
            <div className="card-hdr">
              <h4>DQ dimension scorecard</h4>
              <span className="meta">rolled up across the warehouse</span>
            </div>
            <div className="dqc-list">
              {DIM_ORDER.map(d => {
                const v = dims?.dimensions?.[d.key] ?? null;
                const tone = pctTone(v, summary?.bands?.scorecard_good ?? 90, summary?.bands?.scorecard_watch ?? 75);
                return (
                  <div className={`dqc-dim ${tone}`} key={d.key} title={dims?.basis?.[d.key] ?? d.hint}>
                    <span className="dqc-dim-name">{d.label}</span>
                    <Meter pct={v} tone={tone} />
                    <span className="dqc-dim-pct">{fmtPct(v)}</span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Cross-source reconciliation: compact stacked rows */}
          <div className="card card-elev">
            <div className="card-hdr">
              <h4>Cross-source reconciliation</h4>
              <span className="meta">business-vault same-as links</span>
            </div>
            {recon && recon.checks.length > 0 ? (
              <div className="dqc-list">
                {recon.checks.map(c => {
                  const tone = pctTone(c.agreement_pct, summary?.bands?.agreement_good ?? 85, summary?.bands?.agreement_watch ?? 60);
                  return (
                    <div className="dqc-row" key={c.check}>
                      <div className="dqc-row-head">
                        <div>
                          <div className="dqc-row-name">{c.check}</div>
                          <div className="dqc-row-sys">{c.systems}</div>
                        </div>
                        <div className={`dqc-row-pct ${tone}`}>{fmtPct(c.agreement_pct)}</div>
                      </div>
                      <Meter pct={c.agreement_pct} tone={tone} />
                      <div className="dqc-row-foot">
                        <span>{fmtNum(c.matched)} of {fmtNum(c.conformed)} matched by at least 2 sources</span>
                        <span className="dqc-chips">
                          {c.sources.map(s => (
                            <span className="dqc-chip" key={s.source}>{s.source} · {fmtNum(s.count)}</span>
                          ))}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="dqc-empty">
                {recon?.note ?? 'Loading cross-source agreement…'}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Incidents & governance KPIs (durable record) */}
      <IncidentsSection flagsResp={flagsResp} kpi={kpi}
        onDetect={async () => {
          setDetectBusy(true); setDetectMsg(null);
          try {
            const r = await dq.runDetect();
            setDetectMsg(`ran in ${r.latency_ms} ms · ${r.findings} findings · ${r.open_flags} open`);
            const [fl2, kp2] = await Promise.all([dq.getFlags('open', 50, 0), dq.getKpi()]);
            setFlagsResp(fl2); setKpi(kp2);
          } catch (e) { setDetectMsg(`detection failed: ${e instanceof Error ? e.message : e}`); }
          finally { setDetectBusy(false); }
        }} detectBusy={detectBusy} detectMsg={detectMsg} />

      {/* dbt tests */}
      <div className="page-section">
        <div className="ps-hdr">
          <div>
            <h3 className="ps-title"><svg className="ico"><use href="#i-list" /></svg> dbt tests · failing &amp; warning</h3>
            <p className="ps-sub">Acknowledge a known issue to record who triaged it and when.</p>
          </div>
          <div className="ps-meta">{failingTests.length === 0
            ? <span className="pill good">all green</span>
            : <>{fails.length} fail · {warns.length} warn</>}</div>
        </div>

        <div className="card card-elev">
          {ackErr && <div className="dq-note dq-note-alert"><svg className="ico ico-sm"><use href="#i-alert" /></svg>{ackErr}</div>}
          {failingTests.length === 0 ? (
            <p className="dq-allgreen">
              <svg className="ico"><use href="#i-check-circle" /></svg>
              No failing or warning tests in the latest <code>dbt build</code>. Pipeline green.
            </p>
          ) : (
            <div className="dq-table-scroll">
              <table className="dash dq-tests">
                <thead>
                  <tr><th>Model</th><th>Test</th><th className="num">Failures</th><th>Severity</th><th>Acknowledge</th></tr>
                </thead>
                <tbody>
                  {failingTests.slice(0, 14).map(t => {
                    const { model, test } = shortTestId(t.unique_id);
                    const sev = t.status === 'fail' || t.status === 'error' ? 'alert' : 'watch';
                    const local = ackLocal[t.unique_id];
                    const acked = t.acked || !!local;
                    const ackedBy = local?.by ?? t.acked_by;
                    const ackedAt = local?.at ?? t.acked_at;
                    return (
                      <tr key={t.unique_id} className={acked ? 'is-acked' : ''}>
                        <td><code>{model}</code></td>
                        <td title={t.message ?? ''}>{test}</td>
                        <td className="num">{t.failures ?? 'n/a'}</td>
                        <td><span className={`pill ${sev}`}>{t.status}</span></td>
                        <td>
                          {acked ? (
                            <span className="dq-acked" title={ackedAt ? fmtAgo(ackedAt) : ''}>
                              <svg className="ico ico-sm"><use href="#i-check" /></svg>
                              acked{ackedBy ? ` · ${ackedBy}` : ''}
                            </span>
                          ) : (
                            <button className="btn btn-sm btn-ghost dq-ack-btn"
                                    disabled={ackBusy[t.unique_id]}
                                    onClick={() => doAck(t)}>
                              {ackBusy[t.unique_id] ? 'saving…' : 'Acknowledge'}
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* Lineage summary + tools */}
      <div className="page-section">
        <div className="ps-hdr">
          <div>
            <h3 className="ps-title"><svg className="ico"><use href="#i-link" /></svg> Lineage &amp; operator tools</h3>
            <p className="ps-sub">
              {lineage
                ? `${lineage.nodes.length} dbt nodes · ${lineage.edges.length} edges` +
                  (lineage.as_of ? ` · as of ${fmtAgo(lineage.as_of)}` : '')
                : 'Loading dbt manifest…'}
            </p>
          </div>
        </div>

        <div className="card card-elev dq-lineage-card">
          <div className="dq-lineage-summary">
            {tierChips.map(c => (
              <div className="dq-tier" key={c.tier}>
                <span className="dq-tier-n">{c.n}</span>
                <span className="dq-tier-lbl">{TIER_LABELS[c.tier as dq.LineageNode['tier']] ?? c.tier}</span>
                {c.tier !== 'source' && <svg className="ico ico-sm dq-tier-arrow"><use href="#i-arrow-right" /></svg>}
              </div>
            ))}
            {tierChips.length === 0 && <span className="muted">{lineage?.note ?? 'Lineage graph loading…'}</span>}
          </div>

          <div className="dq-lineage-rollup">
            <span className="dq-lineage-lbl">Model test status</span>
            <span className="pill good">{modelTestRollup.pass} pass</span>
            {modelTestRollup.warn > 0 && <span className="pill watch">{modelTestRollup.warn} warn</span>}
            {modelTestRollup.fail > 0 && <span className="pill alert">{modelTestRollup.fail} fail</span>}
            {modelTestRollup.untested > 0 && <span className="pill neutral">{modelTestRollup.untested} untested</span>}
            <a href={toolUrl('dbt-docs', "/")} target="_blank" rel="noreferrer" className="btn btn-primary btn-sm dq-dbt-link">
              Open full lineage in dbt Docs <svg className="ico ico-sm"><use href="#i-external" /></svg>
            </a>
          </div>

          <div className="dq-tools">
            {TOOLS.map(t => (
              <a className="dq-tool" href={t.href} target="_blank" rel="noreferrer" key={t.label}>
                <span className="dq-tool-ico"><img src={t.brand} alt="" style={{ width: 20, height: 20, objectFit: 'contain' }} /></span>
                <span className="dq-tool-txt">
                  <span className="dq-tool-name">{t.label} <svg className="ico ico-sm"><use href="#i-external" /></svg></span>
                  <span className="dq-tool-sub">{t.sub}</span>
                </span>
              </a>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function IncidentsSection({ flagsResp, kpi, onDetect, detectBusy, detectMsg }: {
  flagsResp: dq.FlagsResp | null; kpi: dq.DqKpi | null;
  onDetect: () => void; detectBusy: boolean; detectMsg: string | null;
}) {
  const rows = flagsResp?.flags ?? null;
  const kit = useTableKit(rows ?? [], {
    pageSize: 8,
    search: r => `${r.source} ${r.table_name} ${r.error_class} ${r.description}`,
    sortVals: {
      raised_at: r => r.raised_at, source: r => r.source,
      error_class: r => r.error_class, severity: r => r.severity,
    },
    initialSort: { key: 'raised_at', dir: 'desc' },
  });
  const fmtH = (h: number | null) => (h === null || h === undefined ? '·' : `${h}h`);
  return (
    <div className="card dq-card">
      <div className="section-hdr" style={{ display: 'flex', alignItems: 'center' }}>
        <h3 style={{ margin: 0 }}>Incidents &amp; governance KPIs</h3>
        <button className="btn btn-sm" style={{ marginLeft: 'auto' }}
                onClick={onDetect} disabled={detectBusy}>
          <svg className="ico ico-sm"><use href="#i-refresh" /></svg>{' '}
          {detectBusy ? 'Running…' : 'Run detection now'}
        </button>
      </div>
      {detectMsg && <p className="dq-note">{detectMsg}</p>}
      {/* KPI strip (nulls render as), never as 0 (an unmeasured latency is not a fast one) */}
      <div className="grid grid-4 dq-kpis">
        <div className={`kpi kpi-${(flagsResp?.counts?.open ?? 0) > 0 ? 'watch' : 'good'}`}>
          <div className="kpi-lbl">Open incidents</div>
          <div className="kpi-val">{flagsResp ? (flagsResp.counts.open ?? 0) : '·'}</div>
          <div className="kpi-delta">{kpi ? `${kpi.reopened_fingerprints} re-opened all-time` : ''}</div>
        </div>
        <div className="kpi kpi-info">
          <div className="kpi-lbl">Time to acknowledge</div>
          <div className="kpi-val">{kpi ? fmtH(kpi.avg_hours_to_ack) : '·'}</div>
        </div>
        <div className="kpi kpi-info">
          <div className="kpi-lbl">Time to resolve</div>
          <div className="kpi-val">{kpi ? fmtH(kpi.avg_hours_to_resolve) : '·'}</div>
          <div className="kpi-delta">{kpi ? `${kpi.resolved_total} resolved all-time` : ''}</div>
        </div>
        <div className={`kpi kpi-${(kpi?.steward?.backlog ?? 0) > 100 ? 'watch' : 'info'}`}>
          <div className="kpi-lbl">Steward backlog</div>
          <div className="kpi-val">{kpi?.steward?.backlog ?? '·'}</div>
          <div className="kpi-delta">{kpi?.steward ? `${kpi.steward.decisions ?? 0} decisions made` : 'MDM stores unavailable'}</div>
        </div>
      </div>
      {rows === null ? (
        <p className="dq-note">Incident queue not yet available on this cluster (init 38 / gateway update pending).</p>
      ) : rows.length === 0 ? (
        <p className="dq-note">No open incidents: the detector last found nothing above thresholds.</p>
      ) : (
        <>
          <TableToolbar kit={kit} placeholder="Filter incidents…" />
          <div className="tk-scroll">
            <table className="dash">
              <thead>
                <tr>
                  <SortTh kit={kit} k="raised_at">Raised</SortTh>
                  <SortTh kit={kit} k="source">Source</SortTh>
                  <th>Table</th>
                  <SortTh kit={kit} k="error_class">Class</SortTh>
                  <SortTh kit={kit} k="severity">Severity</SortTh>
                  <th>Description</th>
                  <th>History</th>
                </tr>
              </thead>
              <tbody>
                {kit.pageRows.map(r => (
                  <tr key={r.id}>
                    <td title={r.raised_at} style={{ whiteSpace: 'nowrap' }}>{fmtAgo(r.raised_at)}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>{r.source}</td>
                    <td><span className="mono text-xs">{r.table_name}</span></td>
                    <td><span className="dq-chip">{r.error_class}</span></td>
                    <td><span className={`dq-chip dq-chip-${r.severity === 'error' ? 'alert' : r.severity === 'warn' ? 'watch' : 'flat'}`}>{r.severity}</span></td>
                    <td>{r.description}</td>
                    <td className="muted" style={{ whiteSpace: 'nowrap' }}>{r.events} events · {r.acks} acks</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <TablePager kit={kit} />
        </>
      )}
    </div>
  );
}


/* widget-module registration (admin catalog runtime)
   One complex surface is one grantable widget: this module is the page's single catalog widget. */
import type { AdminWidget } from '../../runtime/types';
const widget: AdminWidget = {
  id: 'adm-dq',
  Component: DQ,
};
export default widget;
