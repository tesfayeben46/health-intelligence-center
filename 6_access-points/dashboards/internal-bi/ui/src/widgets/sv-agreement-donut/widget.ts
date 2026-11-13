/**
 * Agreement Overview (Service page): DHIS2/EMR concordance headline.
 * agreement ratio = records within tolerance / compared rows;
 * bands Agree >=95 · Moderate 80 to 94 · Low <80. Part of the Reported-vs-Recorded section;
 * EMR-active scope. Source: EMR + DHIS2 + MFR.
 * Data contract, single row:
 *   overall_agreement_pct · agree_pct · moderate_pct · low_pct · delta_pp
 * reactsTo: geography · programme · period.
 */
import * as echarts from 'echarts';
import type { WidgetDef, WidgetCtx } from '../../runtime/types';

const def: WidgetDef = {
  id: 'sv-agreement-donut',
  reactsTo: ['geography', 'programme', 'period'],
  async render(ctx: WidgetCtx) {
    const r = (await ctx.fetchData())?.[0];
    if (!r) { ctx.empty('No EMR/DHIS2 agreement data for the selected filters.'); return; }
    const t = ctx.theme;
    const overall = Number(r.overall_agreement_pct ?? 0);
    const segs = [
      { name: 'Agree >=95%', value: Number(r.agree_pct ?? 0), color: t.status.good },
      { name: 'Moderate 80 to 94%', value: Number(r.moderate_pct ?? 0), color: t.status.watch },
      { name: 'Low <80%', value: Number(r.low_pct ?? 0), color: t.status.alert },
    ];
    const delta = Number(r.delta_pp ?? 0);
    ctx.el.style.cssText = 'display:flex;flex-direction:column;justify-content:center;height:100%';
    ctx.el.innerHTML = `
      <p style="font-size:.82rem;color:${t.ink[2]};margin:0 0 .4rem">Across selected high-priority indicators, EMR-active facilities only</p>
      <div class="sv-ag-ring" style="height:252px"></div>
      <div class="sv-ag-legend" style="display:flex;flex-wrap:wrap;justify-content:center;gap:.4rem 1.2rem;margin-top:.4rem"></div>
      <div style="text-align:center;margin-top:.5rem;font-size:.74rem;font-weight:600;color:${delta >= 0 ? t.status.good : t.status.alert}">${delta >= 0 ? '+' : ''}${delta} pp vs last quarter</div>`;
    const legend = ctx.el.querySelector('.sv-ag-legend') as HTMLElement;
    legend.innerHTML = segs.map(s => `<span style="display:inline-flex;align-items:center;gap:.4rem;font-size:.78rem;color:${t.ink[1]}">
      <span style="width:9px;height:9px;border-radius:2px;background:${s.color}"></span>${s.name}</span>`).join('');
    const chart = echarts.init(ctx.el.querySelector('.sv-ag-ring') as HTMLElement);
    chart.setOption({
      textStyle: { fontFamily: t.fontSans },
      title: { text: `${overall}%`, subtext: 'Overall agreement', left: 'center', top: '39%', textStyle: { fontSize: 28, fontWeight: 800, color: t.ink[0] }, subtextStyle: { fontSize: 10, color: t.ink[3] } },
      tooltip: { trigger: 'item', formatter: '{b}<br/><b>{c}%</b>' },
      series: [{ type: 'pie', radius: ['46%', '68%'], center: ['50%', '46%'], avoidLabelOverlap: true,
        label: { show: true, position: 'outside', formatter: '{c}%', fontSize: 11, color: t.ink[1] },
        labelLine: { show: true, length: 8, length2: 8, lineStyle: { color: t.line } },
        data: segs.map(s => ({ value: s.value, name: s.name, itemStyle: { color: s.color, borderColor: t.paper, borderWidth: 2 } })) }],
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
