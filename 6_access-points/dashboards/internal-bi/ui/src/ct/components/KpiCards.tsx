import { summarize, filterMovements, type Slicer } from '../lib/select';
import { KPIS, NODES, WH_BY_ID } from '../data/runtime';
import { IconCoverBar, IconPallet, IconDepotFan, IconStockShort, IconShieldCheck, IconAlertTriangle, IconRunout, IconGauge } from '../icons';

const fmt = (n: number) => Math.round(n).toLocaleString('en-US');

const RING = { c: 150.8, good: '#15865b', mid: '#9a6a3a', bad: '#c73545', none: '#9ca3af' };

/** Open donut ring in the icon slot's exact 58px footprint: track + arc + centre icon.
 *  Distinct from row 1's filled pastel discs; the colour carries the reading. */
function Ring({ f, color, label, value, Icon }:
  { f: number | null; color: string; label: string; value: string; Icon: (p: any) => JSX.Element }) {
  const fill = f == null ? 0 : Math.min(1, Math.max(0.02, f));
  return (
    <span className="pct-kpi-ring" style={{ color }} role="img" aria-label={`${label}: ${value}`}>
      <svg className="ring" viewBox="0 0 58 58" aria-hidden="true">
        <circle cx="29" cy="29" r="24" fill="none" stroke="#EAEDF1" strokeWidth="5" />
        <circle cx="29" cy="29" r="24" fill="none" stroke={color} strokeWidth="5" strokeLinecap="round"
          strokeDasharray={RING.c} strokeDashoffset={RING.c * (1 - fill)} transform="rotate(-90 29 29)" />
      </svg>
      <Icon aria-hidden="true" />
    </span>
  );
}

/** Month-over-month movement in points: arrow + magnitude + colour, nothing else. */
function Delta({ d, goodWhenUp }: { d: number | null; goodWhenUp: boolean }) {
  if (d == null || Math.abs(d) < 0.05) return null;
  const good = goodWhenUp ? d > 0 : d < 0;
  return (
    <span className="pct-kpi-delta" style={{ color: good ? RING.good : RING.bad }}>
      {d > 0 ? '▲' : '▼'} {Math.abs(d).toFixed(1)}
    </span>
  );
}

// Each card carries one informative count as its secondary line.
export default function KpiCards({ slicer }: { slicer: Slicer }) {
  const movs = filterMovements(slicer);
  const s = summarize(movs);
  const cards = [
    { key: 'cover', tint: 'green', Icon: IconCoverBar, label: 'Needs covered',
      value: s.coveredPct != null ? `${Math.round(s.coveredPct)}%` : 'no data', sub: `${fmt(s.uncovered)} at-risk needs open` },
    { key: 'units', tint: 'blue', Icon: IconPallet, label: 'Units to ship',
      value: fmt(s.units), sub: `${fmt(s.legs)} deliveries` },
    { key: 'runs', tint: 'amber', Icon: IconDepotFan, label: 'Delivery runs',
      value: fmt(s.runCount), sub: `${fmt(s.stops)} facilities served` },
    { key: 'risk', tint: 'violet', Icon: IconStockShort, label: 'Facilities at risk',
      value: fmt(s.facilitiesAtRisk), sub: `of ${fmt(s.facilities)} facilities` },
  ];

  // Stock-health row: rank-free pooled numerator/denominator per month, exactly as the
  // KPI definitions say; region scope follows the local filters.
  const regionName = slicer.region !== 'all' ? slicer.region
    : slicer.hub !== 'all' ? WH_BY_ID[slicer.hub]?.zone : null;
  const regionId = regionName ? NODES.find(n => n.region === regionName)?.regionId ?? null : null;
  const months = [...new Set(KPIS.map(k => k.month).filter(Boolean))].sort() as string[];
  const now = months[months.length - 1]; const prev = months.length > 1 ? months[0] : null;
  const pooled = (kpi: string, month: string | null, category?: string) => {
    if (!month) return null;
    let n = 0, d = 0;
    for (const k of KPIS) {
      if (k.kpi !== kpi || k.month !== month || (category !== undefined && k.category !== category)) continue;
      if (regionId && k.regionId !== regionId) continue;
      n += k.numerator || 0; d += k.denominator || 0;
    }
    return d > 0 ? (100 * n) / d : null;
  };
  const delta = (kpi: string, category?: string) => {
    const a = pooled(kpi, now, category), b = pooled(kpi, prev, category);
    return a != null && b != null ? a - b : null;
  };
  const pct = (v: number | null) => (v == null ? 'no data' : `${v.toFixed(1)}%`);
  const target = KPIS.find(k => k.kpi === 'osa_s8' && k.target != null)?.target ?? 90;
  const osa = pooled('osa_s8', now); const exposure = pooled('stockout_exposure', now);
  const days = pooled('stockout_days_rate', now); const policy = pooled('mos_band', now, 'within_policy');
  const ringColor = (v: number | null, good: number, mid: number, lowIsGood: boolean) => {
    if (v == null) return RING.none;
    if (lowIsGood) return v <= good ? RING.good : v <= mid ? RING.mid : RING.bad;
    return v >= good ? RING.good : v >= mid ? RING.mid : RING.bad;
  };
  const health = [
    { key: 'osa', Icon: IconShieldCheck, label: 'On-shelf availability', value: pct(osa),
      sub: `Target ${target}%`, f: osa != null ? osa / 100 : null,
      color: ringColor(osa, target, target - 5, false), d: delta('osa_s8'), up: true },
    { key: 'exp', Icon: IconAlertTriangle, label: 'Stockout exposure', value: pct(exposure),
      sub: 'Tracer facility-products', f: exposure != null ? exposure / 100 : null,
      color: ringColor(exposure, 10, 25, true), d: delta('stockout_exposure'), up: false },
    { key: 'days', Icon: IconRunout, label: 'Stockout days', value: days == null ? 'no data' : days.toFixed(1),
      sub: 'Per 100 days', f: days != null ? Math.min(days / 100, 1) : null,
      color: ringColor(days, 5, 20, true), d: delta('stockout_days_rate'), up: false },
    { key: 'pol', Icon: IconGauge, label: 'Stock within policy', value: pct(policy),
      sub: 'Max-min band', f: policy != null ? policy / 100 : null,
      color: ringColor(policy, 70, 40, false), d: delta('mos_band', 'within_policy'), up: true },
  ];

  return (
    <div className="pct-kpis">
      {cards.map(c => (
        <div key={c.key} className="pct-kpi">
          <span className={`pct-kpi-ico ${c.tint}`}><c.Icon /></span>
          <div className="pct-kpi-body">
            <span className="pct-kpi-lbl">{c.label}</span>
            <span className="pct-kpi-val">{c.value}</span>
            <span className="pct-kpi-sub">{c.sub}</span>
          </div>
        </div>
      ))}
      {health.map(c => (
        <div key={c.key} className="pct-kpi">
          <Ring f={c.f} color={c.color} label={c.label} value={c.value} Icon={c.Icon} />
          <div className="pct-kpi-body">
            <span className="pct-kpi-lbl">{c.label}</span>
            <span className="pct-kpi-valrow"><span className="pct-kpi-val">{c.value}</span><Delta d={c.d} goodWhenUp={c.up} /></span>
            <span className="pct-kpi-sub">{c.sub}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
