/**
 * Wastage & Consistency (Resource page).
 * The wastage treemap is paired with commodity-use vs service-output consistency
 * (consumption observed vs expected from service volume), so mismatches read as
 * audit signals, not accusations. Source: bi_supply + DHIS2 (+ EMR where available).
 *
 * Data contract: commodity · wastage_score:num · use_band:'adequate'|'high'|'low'
 * (the use-vs-service mix is derived from the use_band distribution). reactsTo: geography · period · commodity.
 */
import * as echarts from 'echarts';
import type { WidgetDef, WidgetCtx } from '../../runtime/types';

const def: WidgetDef = {
  id: 'rm-wastage',
  reactsTo: ['geography', 'period', 'commodity'],
  async render(ctx: WidgetCtx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No wastage and consistency data for the selected filters.'); return; }
    const t = ctx.theme;
    const ramp = t.ramps.wastageTreemap;              // high to low wastage
    const max = Math.max(...rows.map(r => Number(r.wastage_score ?? 0))) || 1;
    const colorFor = (v: number) => ramp[Math.min(ramp.length - 1, Math.round((1 - v / max) * (ramp.length - 1)))];

    // Use-vs-service mix = share of commodities in each band.
    const total = rows.length;
    const band = (b: string) => rows.filter(r => String(r.use_band) === b).length;
    const pct = (n: number) => Math.round((n / total) * 100);
    const mix = [
      { label: 'Adequate use', sub: 'within expected range', v: pct(band('adequate')), col: t.status.good },
      { label: 'High use', sub: 'above expected range', v: pct(band('high')), col: t.status.watch },
      { label: 'Low use', sub: 'below expected range', v: pct(band('low')), col: t.status.info },
    ];

    ctx.el.innerHTML = `
      <div style="display:flex;gap:1rem">
        <div class="rm-wt-tree" style="flex:1;min-width:0;height:300px"></div>
        <div style="width:170px;flex-shrink:0;display:flex;flex-direction:column;justify-content:center;gap:.7rem">
          <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:${t.ink[3]}">Use vs service output</div>
          ${mix.map(m => `<div>
            <div style="font-size:1.4rem;font-weight:800;color:${m.col};font-variant-numeric:tabular-nums;line-height:1">${m.v}%</div>
            <div style="font-size:.76rem;font-weight:600;color:${t.ink[1]}">${m.label}</div>
            <div style="font-size:.7rem;color:${t.ink[3]}">${m.sub}</div></div>`).join('')}
        </div>
      </div>`;

    const chart = echarts.init(ctx.el.querySelector('.rm-wt-tree') as HTMLElement);
    chart.setOption({
      textStyle: { fontFamily: t.fontSans },
      tooltip: { formatter: (p: any) => `${p.name}<br/>wastage <b>${p.value}</b>` },
      series: [{
        type: 'treemap', roam: false, nodeClick: false, breadcrumb: { show: false },
        width: '100%', height: '100%', top: 0, left: 0, right: 0, bottom: 0,
        itemStyle: { borderColor: t.paper, borderWidth: 1, gapWidth: 1 },
        label: { show: true, formatter: '{b}\n{c}', fontSize: 11, color: '#fff', fontWeight: 600, overflow: 'truncate' },
        data: rows.map(r => ({ name: String(r.commodity ?? '-'), value: Number(r.wastage_score ?? 0), itemStyle: { color: colorFor(Number(r.wastage_score ?? 0)) } })),
      }],
    });
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    // Follow the CARD's size too: a scope reflow (global-filter reactivity)
    // resizes the grid without a window resize; same treatment as the shared
    // bindResize in _shared/charts.ts.
    let ro: ResizeObserver | undefined;
    try { ro = new ResizeObserver(() => chart.resize()); ro.observe(chart.getDom()); } catch { /* window resize covers it */ }
    return () => { window.removeEventListener('resize', onResize); ro?.disconnect(); chart.dispose(); };
  },
};
export default def;
