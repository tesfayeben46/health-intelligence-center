/**
 * Widget · pub-insights: KPI tiles + region equity bar + 24-month trend.
 *
 * The tiles, region bar and trend read the k-anonymised public marts through
 * the widget-data seam; nothing is hardcoded when the gateway responds:
 *   - `pub-insights`: bi_public.mart_public_indicator_kpis rolled to national
 *      by indicator by month (rate_pct + HSDIP target + definition). Drives the
 *      KPI tiles (latest + prior month) and the indicator trend.
 *   - `pub-region`: the same mart at region by coverage-indicator (latest
 *      month) for the equity bar.
 * Every value traces mean_value to DHIS2. Reporting completeness is rendered as
 * a distinct trust tile, not among the coverage KPIs (a data-quality measure
 * must never read as coverage). Built-in sample arrays are used only as the
 * offline fallback when the gateway returns nothing usable. This widget takes nothing
 * from the host: it is fully self-contained on the widget-data seam.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
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

/** pub-insights rows (national by indicator by month) to KPI tiles. */
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
    const kind = String(latest.kpi_kind || 'outcome');
    const delta = prev && prev.rate_pct != null ? val - Number(prev.rate_pct) : null;
    tiles.push({
      key: id, kind, order: Number(latest.kpi_order || 99),
      tone: kind === 'trust' ? 'info' : toneFor(val, target, dir),
      lbl: String(latest.kpi_label || id),
      val: `${Math.round(val)}%`,
      deltaTrend: delta == null ? 'flat' : delta > 0.05 ? 'up' : delta < -0.05 ? 'down' : 'flat',
      deltaText: delta == null ? 'no prior month'
        : `${delta >= 0 ? '+' : '−'}${Math.abs(delta).toFixed(1)}pp MoM`,
      target: target == null ? ''
        : `${String(latest.target_source || 'HSDIP')} target ${dir === 'lower_better' ? '<=' : ''}${target}%`,
      conf: latest.facilities_reporting != null
        ? `${Number(latest.facilities_reporting).toLocaleString('en-US')} facilities reporting` : undefined,
      def: latest.definition ? String(latest.definition) : undefined,
    });
  }
  tiles.sort((a, b) => a.order - b.order);
  return tiles.length ? tiles : undefined;
}

/** pub-insights rows to trend ChartData (coverage indicators over months). */
function adaptTrend(d: WidgetData | null): ChartData | undefined {
  const need = ['indicator_id', 'kpi_label', 'kpi_kind', 'month_id', 'rate_pct'];
  if (!d || !need.every(c => d.columns.includes(c))) return undefined;
  const cov = d.rows.filter(r => String(r.kpi_kind) === 'coverage');
  const months = Array.from(new Set(cov.map(r => String(r.month_id))
    .filter(m => m && m !== 'null'))).sort().slice(-24);
  const inds = Array.from(new Set(cov.map(r => String(r.kpi_label)).filter(x => x && x !== 'null')));
  if (!months.length || !inds.length) return undefined;
  const series = inds.map(ind => ({
    name: ind,
    data: months.map(m => {
      const row = cov.find(r => String(r.kpi_label) === ind && String(r.month_id) === m);
      return row?.rate_pct == null ? null : Number(row.rate_pct);
    }),
  }));
  return { x: months, series };
}

/** pub-region rows (region by coverage indicator, latest month) to bar ChartData. */
function adaptRegion(d: WidgetData | null): ChartData | undefined {
  const need = ['region_name', 'kpi_label', 'rate_pct'];
  if (!d || !need.every(c => d.columns.includes(c))) return undefined;
  const inds = Array.from(new Set(d.rows.map(r => String(r.kpi_label)).filter(x => x && x !== 'null')));
  // sort regions ascending by the first indicator so the worst-served are visible
  const regions = Array.from(new Set(d.rows.map(r => String(r.region_name)).filter(x => x && x !== 'null')));
  const firstInd = inds[0];
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

type Supply = { stockedPct: number; hubs: number; stocked: number; totalFacilities: number; totalRegions: number };
/** pub-supply row to national service-readiness summary. */
function adaptSupply(d: WidgetData | null): Supply | null {
  if (!d || !d.columns.includes('stocked_pct') || !d.rows.length) return null;
  const r = d.rows[0];
  const totalFacilities = Number(r.total_facilities) || 0;
  if (!totalFacilities) return null;
  return {
    stockedPct: Number(r.stocked_pct), hubs: Number(r.n_facilities),
    stocked: Number(r.n_stocked), totalFacilities,
    totalRegions: Number(r.total_regions) || 0,
  };
}

// Offline fallback, used only when the gateway returns nothing and the active
// profile has no `insights.fallback_kpis`. Country figures live in each
// profile's copy deck (profileKpis below) with their source named, so this
// last-resort set is country-neutral: generic labels and no values.
const KPIS_FALLBACK: KPI[] = [
  { key: 'PENTA3-COV', kind: 'coverage', order: 1, tone: 'info', lbl: 'Penta3 coverage', val: 'n/a', deltaTrend: 'flat', deltaText: 'national estimate', target: '' },
  { key: 'ANC4-COV', kind: 'coverage', order: 2, tone: 'info', lbl: 'ANC4 coverage', val: 'n/a', deltaTrend: 'flat', deltaText: 'national estimate', target: '' },
  { key: 'SBA-COV', kind: 'coverage', order: 3, tone: 'info', lbl: 'Skilled birth attendance', val: 'n/a', deltaTrend: 'flat', deltaText: 'national estimate', target: '' },
  { key: 'FP-MCPR', kind: 'coverage', order: 4, tone: 'info', lbl: 'Modern contraceptive use', val: 'n/a', deltaTrend: 'flat', deltaText: 'national estimate', target: '' },
  { key: 'PHEM-COMPL', kind: 'trust', order: 8, tone: 'info', lbl: 'Reporting completeness', val: 'n/a', deltaTrend: 'flat', deltaText: 'routine reporting', target: '' },
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
      {k.conf && <div className="kpi-conf">{k.conf}</div>}
    </div>
  );
}

function PublicInsights() {
  const { profile, copy } = useProfile();
  // "region" in Ethiopia, "province" in Zambia: the admin-1 label from the
  // profile, used wherever this widget names the sub-national level in its own
  // markup instead of saying "region".
  const a1 = profile.geography?.levels?.find(l => l.code === 'admin1');
  const levelLabel = (a1?.singular ?? 'region').toLowerCase();
  const levelPluralLower = (a1?.plural ?? 'regions').toLowerCase();
  const regionRef = useRef<HTMLDivElement>(null);
  const trendRef = useRef<HTMLDivElement>(null);
  const insightsData = useWidgetData('pub-insights');
  const regionData = useWidgetData('pub-region');
  const supplyData = useWidgetData('pub-supply');

  const profileKpis = copy?.insights?.fallback_kpis as KPI[] | undefined;
  // `fallback_only` marks a country whose coverage marts are not in this
  // deployment (e.g. a profile onboarded at the presentation layer before its
  // data layer is ingested). It renders the profile's own published figures and
  // IGNORES the live widget-data seam, so the shared marts of another country
  // are never surfaced under this one. et-v3/zm-v1 do not set it and are
  // unaffected.
  const fallbackOnly = copy?.insights?.fallback_only === true;
  const kpis = useMemo(
    () => (fallbackOnly ? null : adaptKpis(insightsData)) ?? (profileKpis?.length ? profileKpis : KPIS_FALLBACK),
    [insightsData, profileKpis, fallbackOnly],
  );
  const trendLive = useMemo(() => (fallbackOnly ? undefined : adaptTrend(insightsData)), [insightsData, fallbackOnly]);
  const regionLive = useMemo(() => (fallbackOnly ? undefined : adaptRegion(regionData)), [regionData, fallbackOnly]);
  // The service-readiness strip is live supply/facility data too: gate it on
  // fallback_only so a dataless country never shows another country's facility
  // counts and hub stock.
  const supply = useMemo(() => (fallbackOnly ? null : adaptSupply(supplyData)), [supplyData, fallbackOnly]);

  const coverage = kpis.filter(k => k.kind !== 'trust');
  const trust = kpis.filter(k => k.kind === 'trust');
  const confidence = trust[0];
  const confidenceVal = confidence ? parseInt(confidence.val, 10) : null;
  const [meterW, setMeterW] = useState(0);

  useEffect(() => {
    if (regionRef.current) regionChart(regionRef.current, regionLive);
  }, [regionLive]);
  useEffect(() => {
    if (trendRef.current) trendChart(trendRef.current, trendLive);
  }, [trendLive]);
  // Animate the confidence meter fill left to right once the value is known.
  useEffect(() => {
    const id = setTimeout(() => setMeterW(confidenceVal ?? 0), 120);
    return () => clearTimeout(id);
  }, [confidenceVal]);

  return (
    <section id="headline" className="page-section page-section--insights">
      <div className="ins-hdr">
        <div>
          <p className="ins-eyebrow"><span className="ins-rule"></span> Health of the nation, in numbers</p>
          <h2 className="ins-h2">Public insights</h2>
          <p className="ins-sub">{copy?.insights?.section_sub
            ?? 'National coverage figures from the latest Demographic and Health Survey, with programme indicators from the warehouse marts.'}
            {' '}Aggregated to national and {levelLabel} level, k-anonymity of at least 5.</p>
        </div>
      </div>

      <div className="grid grid-4">
        {coverage.map(k => <Tile key={k.key} k={k} />)}
        {confidenceVal != null && (
          <div className="kpi kpi-info pi-conf-tile" title={confidence.def}>
            <div className="kpi-lbl">Data confidence</div>
            <div className="kpi-val">{confidence.val}</div>
            <div className="conf-meter"><span className="conf-fill" style={{ width: `${meterW}%` }} /></div>
            <div className="kpi-tgt">Reporting completeness, not a coverage rate</div>
          </div>
        )}
      </div>

      {supply && (
        <div className="trust-strip readiness-strip">
          <span className="trust-strip-lbl">Service readiness</span>
          <div className="trust-tile">
            <span className="trust-tile-val">{supply.totalFacilities.toLocaleString('en-US')}</span>
            <span className="trust-tile-lbl">health facilities · {supply.totalRegions} regions</span>
          </div>
          <div className="trust-tile">
            <span className="trust-tile-val">{Math.round(supply.stockedPct)}%</span>
            <span className="trust-tile-lbl">commodity availability · {supply.stocked}/{supply.hubs} supply hubs stocked</span>
          </div>
          <span className="trust-strip-note">Facility coverage and commodity availability, aggregate and k-anonymity-protected.</span>
        </div>
      )}

      <div className="grid grid-2 mt-lg">
        <div className="card card-elev">
          <div className="card-hdr">
            <h4>Coverage by {levelLabel}, latest period</h4>
            {regionLive && (
              <span className="meta">{`${regionLive.series.length} indicators · ${regionLive.x.length} ${levelPluralLower}`}</span>
            )}
          </div>
          {regionLive
            ? <div id="region-chart" ref={regionRef} className="ch-lg"></div>
            : <div className="od-empty" style={{ minHeight: 160, display: 'flex', flexDirection: 'column',
                       alignItems: 'center', justifyContent: 'center', textAlign: 'center' }}>
                <p className="text-sm muted">Coverage by {levelLabel} appears once monthly indicator data is published.</p>
              </div>}
        </div>
        <div className="card card-elev">
          <div className="card-hdr">
            <h4>National headline trends, last 24 months</h4>
            {trendLive && <span className="meta">{`Monthly · ${trendLive.series.length} indicators`}</span>}
          </div>
          {trendLive
            ? <div id="trend-chart" ref={trendRef} className="ch-lg"></div>
            : <div className="od-empty" style={{ minHeight: 160, display: 'flex', flexDirection: 'column',
                     alignItems: 'center', justifyContent: 'center', textAlign: 'center' }}>
                <p className="text-sm muted">Monthly indicator trends appear once a time series is published.</p>
              </div>}
        </div>
      </div>
    </section>
  );
}

const widget: PublicWidget = { id: 'pub-insights', Component: PublicInsights };
export default widget;
