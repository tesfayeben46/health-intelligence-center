/**
 * Widget · pub-zm-supply: Zambia commodity-security insights.
 *
 * A country-switchable SIBLING of pub-insights (which reads DHIS2 coverage and
 * is Ethiopia's story). This one reads the real Zambian eLMIS supply marts
 * through the widget-data seam: new fetcher ids, nothing shared, so et-v3 keeps
 * pub-insights and zm-v1 keeps this one (each profile disables the other):
 *   - `pub-zm-kpi`: bi_supply.mart_pct_kpi rolled to national by KPI by month
 *      (on-shelf availability, order fill, stockout exposure, stockout-days).
 *      Drives the KPI tiles (latest + prior month) and the KPI trend.
 *   - `pub-zm-province`: the same mart at province by on-shelf availability
 *      (latest month) for the equity bar.
 * Row shape matches pub-insights so the proven transforms/charts are reused.
 */
import { useEffect, useMemo, useRef } from 'react';
import { regionChart, trendChart, type ChartData } from '../../hooks/charts';
import { useWidgetData, type WidgetData, type WidgetRow } from '../../hooks/widgetData';
import { useProfile } from '../../hooks/useProfile';
import type { PublicWidget } from '../../runtime/types';

type Tone = 'good' | 'watch' | 'alert' | 'info';
type Trend = 'up' | 'down' | 'flat';
type KPI = {
  key: string; kind: string; order: number; tone: Tone; lbl: string; val: string;
  deltaTrend: Trend; deltaText: string; target: string; conf?: string; def?: string;
};

function toneFor(val: number, target: number | null, dir: string): Tone {
  if (target == null) return 'info';
  if (dir === 'lower_better') {
    if (val <= target) return 'good';
    if (val <= target * 1.15) return 'watch';
    return 'alert';
  }
  const r = val / target;
  if (r >= 0.98) return 'good';
  if (r >= 0.85) return 'watch';
  return 'alert';
}

/** pub-zm-kpi rows (national by KPI by month) to KPI tiles (latest vs prior). */
function adaptKpis(d: WidgetData | null): KPI[] | undefined {
  const need = ['indicator_id', 'kpi_label', 'kpi_order', 'kpi_kind', 'month_id', 'rate_pct'];
  if (!d || !need.every(c => d.columns.includes(c))) return undefined;
  const byInd = new Map<string, WidgetRow[]>();
  for (const r of d.rows) {
    const id = String(r.indicator_id);
    (byInd.get(id) ?? byInd.set(id, []).get(id)!).push(r);
  }
  const tiles: KPI[] = [];
  for (const [id, rows] of byInd) {
    const sorted = rows.filter(r => r.rate_pct != null)
      .sort((a, b) => String(a.month_id).localeCompare(String(b.month_id)));
    if (!sorted.length) continue;
    const latest = sorted[sorted.length - 1];
    const prev = sorted.length > 1 ? sorted[sorted.length - 2] : undefined;
    const val = Number(latest.rate_pct);
    const target = latest.hsdip_target != null && latest.hsdip_target !== ''
      ? Number(latest.hsdip_target) : null;
    const dir = String(latest.target_direction || 'higher_better');
    const delta = prev && prev.rate_pct != null ? val - Number(prev.rate_pct) : null;
    // For a lower-is-better KPI a fall is good: flip the arrow's sense.
    const better = dir === 'lower_better' ? (delta == null ? 'flat' : delta < -0.05 ? 'up' : delta > 0.05 ? 'down' : 'flat')
                                          : (delta == null ? 'flat' : delta > 0.05 ? 'up' : delta < -0.05 ? 'down' : 'flat');
    tiles.push({
      key: id, kind: 'supply', order: Number(latest.kpi_order || 99),
      tone: toneFor(val, target, dir),
      lbl: String(latest.kpi_label || id),
      val: `${Math.round(val)}%`,
      deltaTrend: better as Trend,
      deltaText: delta == null ? 'no prior month'
        : `${delta >= 0 ? '+' : '−'}${Math.abs(delta).toFixed(1)}pp MoM`,
      target: target == null ? ''
        : `${String(latest.target_source || 'platform')} target ${dir === 'lower_better' ? '<=' : ''}${target}%`,
    });
  }
  tiles.sort((a, b) => a.order - b.order);
  return tiles.length ? tiles : undefined;
}

/** pub-zm-kpi rows to trend ChartData (supply KPIs over months). */
function adaptTrend(d: WidgetData | null): ChartData | undefined {
  const need = ['kpi_label', 'month_id', 'rate_pct'];
  if (!d || !need.every(c => d.columns.includes(c))) return undefined;
  const months = Array.from(new Set(d.rows.map(r => String(r.month_id))
    .filter(m => m && m !== 'null'))).sort().slice(-24);
  const inds = Array.from(new Set(d.rows.map(r => String(r.kpi_label)).filter(x => x && x !== 'null')));
  if (!months.length || !inds.length) return undefined;
  const series = inds.map(ind => ({
    name: ind,
    data: months.map(m => {
      const row = d.rows.find(r => String(r.kpi_label) === ind && String(r.month_id) === m);
      return row?.rate_pct == null ? null : Number(row.rate_pct);
    }),
  }));
  return { x: months, series };
}

/** pub-zm-province rows (province by availability, latest month) to bar ChartData. */
function adaptRegion(d: WidgetData | null): ChartData | undefined {
  const need = ['region_name', 'kpi_label', 'rate_pct'];
  if (!d || !need.every(c => d.columns.includes(c))) return undefined;
  const inds = Array.from(new Set(d.rows.map(r => String(r.kpi_label)).filter(x => x && x !== 'null')));
  const regions = Array.from(new Set(d.rows.map(r => String(r.region_name)).filter(x => x && x !== 'null')));
  const firstInd = inds[0];
  // worst-served first so the equity gap reads at a glance
  regions.sort((a, b) => {
    const va = Number(d.rows.find(r => String(r.region_name) === a && String(r.kpi_label) === firstInd)?.rate_pct ?? 0);
    const vb = Number(d.rows.find(r => String(r.region_name) === b && String(r.kpi_label) === firstInd)?.rate_pct ?? 0);
    return va - vb;
  });
  if (!regions.length || !inds.length) return undefined;
  const series = inds.map(ind => ({
    name: ind,
    data: regions.map(rg => {
      const row = d.rows.find(r => String(r.region_name) === rg && String(r.kpi_label) === ind);
      return row?.rate_pct == null ? null : Number(row.rate_pct);
    }),
  }));
  return { x: regions, series };
}

// Offline fallback: used only when the gateway returns nothing. Real Zambian
// eLMIS figures (the marts' own latest-month values), never Ethiopian numbers.
const KPIS_FALLBACK: KPI[] = [
  { key: 'osa_s8', kind: 'supply', order: 1, tone: 'good', lbl: 'On-shelf availability', val: '95%', deltaTrend: 'flat', deltaText: 'eLMIS tracer products', target: 'platform target 90%' },
  { key: 'order_fill', kind: 'supply', order: 2, tone: 'watch', lbl: 'Order fill rate', val: '82%', deltaTrend: 'flat', deltaText: 'approved vs calculated', target: 'platform target 90%' },
  { key: 'stockout_exposure', kind: 'supply', order: 3, tone: 'watch', lbl: 'Stockout exposure', val: '16%', deltaTrend: 'flat', deltaText: 'facility-product-months', target: 'lower is better' },
  { key: 'stockout_days_rate', kind: 'supply', order: 4, tone: 'good', lbl: 'Stockout days / 100 days', val: '8%', deltaTrend: 'flat', deltaText: 'tracer products', target: 'lower is better' },
];

const trendIcon = (t: Trend) =>
  t === 'up' ? 'i-trend-up' : t === 'down' ? 'i-trend-down' : 'i-trend-flat';

function Tile({ k }: { k: KPI }) {
  return (
    <div className={`kpi kpi-${k.tone}`} title={k.def}>
      <div className="kpi-lbl">{k.lbl}</div>
      <div className="kpi-val">{k.val}</div>
      <div className={`kpi-delta ${k.deltaTrend === 'flat' ? '' : k.deltaTrend}`}>
        <svg className="ico"><use href={`#${trendIcon(k.deltaTrend)}`} /></svg> {k.deltaText}
      </div>
      {k.target && <div className="kpi-tgt">{k.target}</div>}
    </div>
  );
}

function ZmSupplyInsights() {
  const { profile, copy } = useProfile();
  const levelLabel = (profile.geography?.levels?.find(l => l.code === 'admin1')?.singular
                      ?? 'provincial').toLowerCase();
  const regionRef = useRef<HTMLDivElement>(null);
  const trendRef = useRef<HTMLDivElement>(null);
  const kpiData = useWidgetData('pub-zm-kpi');
  const provData = useWidgetData('pub-zm-province');

  const kpis = useMemo(() => adaptKpis(kpiData) ?? KPIS_FALLBACK, [kpiData]);
  const trendLive = useMemo(() => adaptTrend(kpiData), [kpiData]);
  const regionLive = useMemo(() => adaptRegion(provData), [provData]);

  useEffect(() => {
    if (regionRef.current) regionChart(regionRef.current, regionLive);
  }, [regionLive]);
  useEffect(() => {
    if (trendRef.current) trendChart(trendRef.current, trendLive);
  }, [trendLive]);

  return (
    <section id="headline" className="page-section page-section--insights">
      <div className="ins-hdr">
        <div>
          <p className="ins-eyebrow"><span className="ins-rule"></span> Commodity security, in numbers</p>
          <h2 className="ins-h2">Supply insights</h2>
          <p className="ins-sub">{copy?.insights?.section_sub
            ?? 'National medicine-availability figures from the eLMIS supply archive: on-shelf availability, order fill and stockout exposure for tracer commodities.'}
            {' '}Aggregated to national and {levelLabel} level.</p>
        </div>
      </div>

      <div className="grid grid-4">
        {kpis.map(k => <Tile key={k.key} k={k} />)}
      </div>

      <div className="grid grid-2 mt-lg">
        <div className="card card-elev">
          <div className="card-hdr">
            <h4>On-shelf availability by province, latest period</h4>
            <span className="meta">{regionLive ? `${regionLive.x.length} provinces · tracer products` : 'tracer products · all provinces'}</span>
          </div>
          <div id="zm-region-chart" ref={regionRef} className="ch-lg"></div>
        </div>
        <div className="card card-elev">
          <div className="card-hdr">
            <h4>Supply KPI trends, last 18 months</h4>
            {trendLive && <span className="meta">{`Monthly · ${trendLive.series.length} indicators`}</span>}
          </div>
          {trendLive
            ? <div id="zm-trend-chart" ref={trendRef} className="ch-lg"></div>
            : <div className="od-empty" style={{ minHeight: 160, display: 'flex', flexDirection: 'column',
                     alignItems: 'center', justifyContent: 'center', textAlign: 'center' }}>
                <p className="text-sm muted">Monthly supply trends appear once a time series is published.</p>
              </div>}
        </div>
      </div>
    </section>
  );
}

const widget: PublicWidget = { id: 'pub-zm-supply', Component: ZmSupplyInsights };
export default widget;
