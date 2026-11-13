/**
 * Clinical-guideline compliance (Service page).
 * Compliance = audited encounters fully compliant / audited encounters.
 * Headline + monthly trend + per-programme breakdown; the audited sample basis is shown.
 * Source: audit/quality data.
 * Data contract, rows keyed by `kind`:
 *   kind='trend': month · compliance_pct   ·   kind='programme': programme · compliance_pct
 * reactsTo: geography · programme · period.
 */
import * as echarts from 'echarts';
import type { WidgetDef, WidgetCtx } from '../../runtime/types';

const def: WidgetDef = {
  id: 'sv-compliance',
  reactsTo: ['geography', 'programme', 'period'],
  async render(ctx: WidgetCtx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No audited compliance data for the selected filters.'); return; }
    const t = ctx.theme;
    const trend = rows.filter(r => r.kind === 'trend');
    const prog = rows.filter(r => r.kind === 'programme');
    const latest = trend.length ? Number(trend[trend.length - 1].compliance_pct ?? 0) : 0;
    const prev = trend.length >= 2 ? Number(trend[trend.length - 2].compliance_pct ?? 0) : latest;
    const delta = latest - prev;   // data-derived, not hard-coded

    ctx.el.innerHTML = `
      <p class="widget-lead" style="color:var(--ink-3);font-size:.8rem;margin:0 0 .6rem">Share of audited encounters that fully followed clinical guidelines, with the monthly trend and a per-programme breakdown.</p>
      <div style="display:flex;gap:1rem;align-items:flex-start">
        <div style="flex:1;min-width:0">
          <div style="font-size:2rem;font-weight:800;color:${t.ink[0]};font-variant-numeric:tabular-nums;line-height:1">${latest}%</div>
          <div style="font-size:.74rem;font-weight:600;color:${delta >= 0 ? t.status.good : t.status.alert};margin-top:.15rem">${delta >= 0 ? '▲' : '▼'} ${Math.abs(delta)} pts vs last month</div>
          <div class="sv-cmp-line" style="height:150px;margin-top:.4rem"></div>
        </div>
        <div style="width:210px;flex-shrink:0;display:flex;flex-direction:column;justify-content:center;gap:.5rem;align-self:stretch">
          ${prog.map(p => { const v = Number(p.compliance_pct ?? 0); return `<div style="display:flex;align-items:center;gap:.5rem;font-size:.78rem">
            <span style="width:104px;flex-shrink:0;color:${t.ink[1]};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${p.programme}</span>
            <span style="flex:1;height:8px;border-radius:4px;background:${t.navy['50']};overflow:hidden"><span style="display:block;height:100%;width:${v}%;background:${t.navy['700']}"></span></span>
            <span style="width:32px;text-align:right;font-weight:700;font-variant-numeric:tabular-nums;color:${t.ink[0]}">${v}%</span></div>`; }).join('')}
        </div>
      </div>
      <p style="margin:.5rem 0 0;font-size:.7rem;color:${t.ink[3]}">Based on an audited encounter sample; confirm the clinical-audit source before national use.</p>`;

    const chart = echarts.init(ctx.el.querySelector('.sv-cmp-line') as HTMLElement);
    chart.setOption({
      textStyle: { fontFamily: t.fontSans },
      grid: { left: 30, right: 12, top: 12, bottom: 24 },
      tooltip: { trigger: 'axis', formatter: (p: any) => `${p[0].name}<br/><b>${p[0].value}%</b>` },
      xAxis: { type: 'category', data: trend.map(r => String(r.month)), axisLabel: { fontSize: 9, color: t.ink[3] }, axisTick: { show: false } },
      yAxis: { type: 'value', axisLabel: { fontSize: 9, color: t.ink[3], formatter: '{value}%' }, splitLine: { lineStyle: { color: t.line } } },
      series: [{ type: 'line', smooth: true, symbol: 'circle', symbolSize: 5, data: trend.map(r => Number(r.compliance_pct ?? 0)),
        lineStyle: { color: t.navy['700'], width: 2 }, itemStyle: { color: t.navy['700'] }, areaStyle: { color: 'rgba(51,65,85,0.06)' } }],
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
