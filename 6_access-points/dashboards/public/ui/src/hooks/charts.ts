/**
 * ECharts helpers for the public page. Each takes a container element and
 * mounts (or reuses) the ECharts instance.
 *
 * Colour policy: categorical series use a neutral qualitative ramp, never the
 * status tokens (--good/--watch/--alert), so a red bar never reads as an alert
 * on a neutral indicator chart. Chart chrome (axes, grid, legend) uses the
 * ink/border tokens. The values are duplicated here as constants; keep them in
 * sync with base.css.
 */
import * as echarts from 'echarts';

/** Categorical ramp, matching the internal-BI slate chart palette: three slate
 *  steps for the first three series (Penta3, ANC4, SBA) and a muted teal for
 *  the fourth (mCPR, line only). No warm or status hues. */
const PALETTE = ['#334155', '#64748B', '#94A3B8', '#4E7C87'];
/** Chart chrome tokens (base.css). */
const AXIS = {
  label:  '#374151', // --ink-2  : legend + category labels
  tick:   '#9ca3af', // --ink-4  : numeric tick labels
  grid:   '#e5e7eb', // --border : split/grid lines
  line:   '#d1d5db', // --border-2 : axis lines
};

/** ECharts-shaped live data adapted by the calling widget from a gateway
 *  widget-data payload ({columns, rows}). Undefined means use bundled data. */
export type ChartData = { x: string[]; series: { name: string; data: (number | null)[] }[] };

/** Reuse the instance when the mount effect re-runs (live data arriving,
 *  StrictMode double-invoke) instead of double-initialising the DOM node. */
function mount(el: HTMLElement) {
  return echarts.getInstanceByDom(el) ?? echarts.init(el);
}

/** `live` (pub-region widget data: coverage indicator by region, latest month)
 *  drives the bars. The category axis is one country's admin-1 units, so there
 *  is no bundled fallback: the caller renders this chart only with live data,
 *  and an absent `live` clears the node rather than drawing another country's
 *  regions. */
export function regionChart(el: HTMLElement, live?: ChartData): void {
  const c = mount(el);
  if (!live) { c.clear(); return; }
  const regions = live.x;
  const series = live.series.map((s, i) => ({
    name: s.name, type: 'bar' as const, data: s.data,
    itemStyle: { color: PALETTE[i % PALETTE.length] }, barWidth: 8,
  }));
  c.setOption({
    grid: { left: 90, right: 12, top: 30, bottom: 40 },
    legend: { data: series.map(s => s.name), top: 0, textStyle: { fontSize: 11, color: AXIS.label } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%', fontSize: 11, color: AXIS.tick }, splitLine: { lineStyle: { color: AXIS.grid } } },
    yAxis: { type: 'category', data: regions, axisTick: { show: false }, axisLine: { show: false }, axisLabel: { fontSize: 11, color: AXIS.label } },
    series,
  }, true);
}

/** Radial gauge for reporting completeness ("data confidence"): slate progress
 *  arc on a light track with a centred % readout, no status hues. */
export function gaugeChart(el: HTMLElement, value: number): void {
  const c = mount(el);
  c.setOption({
    series: [{
      type: 'gauge',
      startAngle: 210, endAngle: -30, min: 0, max: 100,
      radius: '96%', center: ['50%', '62%'],
      progress: { show: true, width: 11, itemStyle: { color: PALETTE[0] } }, // #334155 slate
      axisLine: { lineStyle: { width: 11, color: [[1, AXIS.grid]] } },
      pointer: { show: false }, axisTick: { show: false }, splitLine: { show: false },
      axisLabel: { show: false }, anchor: { show: false }, title: { show: false },
      detail: {
        valueAnimation: true, offsetCenter: [0, '-2%'],
        fontSize: 26, fontWeight: 700, color: AXIS.label,
        fontFamily: 'Nunito Sans, system-ui, sans-serif', formatter: '{value}%',
      },
      data: [{ value }],
    }],
  }, true);
}

/** Monthly indicator lines from the widget's live series. Without live data the
 *  chart is cleared; callers show an empty state instead of placeholder lines. */
export function trendChart(el: HTMLElement, live?: ChartData): void {
  const c = mount(el);
  if (!live) { c.clear(); return; }
  const months = live.x;
  const series = live.series.map((s, i) => ({
    name: s.name, type: 'line' as const, smooth: true, symbol: 'none', data: s.data,
    lineStyle: { color: PALETTE[i % PALETTE.length], width: 2.2 },
    // legend swatch follows the line colour
    itemStyle: { color: PALETTE[i % PALETTE.length] },
  }));
  c.setOption({
    grid: { left: 40, right: 12, top: 30, bottom: 32 },
    legend: { data: series.map(s => s.name), top: 0, textStyle: { fontSize: 11, color: AXIS.label } },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: months, axisLabel: { fontSize: 11, color: AXIS.tick }, axisLine: { lineStyle: { color: AXIS.line } } },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: { formatter: '{value}%', fontSize: 11, color: AXIS.tick }, splitLine: { lineStyle: { color: AXIS.grid } } },
    series,
  }, true);
}
