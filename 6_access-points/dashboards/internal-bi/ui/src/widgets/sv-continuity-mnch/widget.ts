/**
 * Continuity of Care: Maternal & Newborn retention pathway (Service page).
 * True cohort leakage beside the aggregate funnel (the same woman who had ANC1 but
 * no ANC4). EMR-active facilities only, not national figures; the card title and
 * scope tag say so. Source: EMR (cohort) + MFR.
 * Data contract: stage · retention_pct:int · scope:str.
 * reactsTo: geography · programme · period.
 */
import * as echarts from 'echarts';
import type { WidgetDef, WidgetCtx } from '../../runtime/types';

const def: WidgetDef = {
  id: 'sv-continuity-mnch',
  reactsTo: ['geography', 'programme', 'period'],
  async render(ctx: WidgetCtx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No EMR cohort records for the selected filters.'); return; }
    const t = ctx.theme;
    const data = rows.map(r => ({ stage: String(r.stage ?? '-'), pct: Number(r.retention_pct ?? 0) }));
    ctx.el.innerHTML = `
      <p class="widget-lead" style="color:var(--ink-3);font-size:.8rem;margin:0 0 .6rem">Share of the maternal and newborn cohort retained at each step, from first contact through postnatal care, so leakage between stages is visible.</p>
      <div style="display:flex;justify-content:flex-end;margin-bottom:.4rem">
        <span style="font-size:.7rem;font-weight:600;color:${t.status.info};background:var(--info-50);border-radius:999px;padding:.15rem .55rem">EMR-active facilities</span>
      </div>
      <div class="sv-cont-mount" style="height:280px"></div>`;
    const chart = echarts.init(ctx.el.querySelector('.sv-cont-mount') as HTMLElement);
    chart.setOption({
      textStyle: { fontFamily: t.fontSans },
      grid: { left: 36, right: 16, top: 24, bottom: 28 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (p: any) => `${p[0].name}<br/><b>${p[0].value}%</b> retained` },
      xAxis: { type: 'category', data: data.map(d => d.stage), axisLabel: { fontSize: 10, color: t.ink[2], interval: 0 }, axisTick: { show: false } },
      yAxis: { type: 'value', max: 100, axisLabel: { fontSize: 9, color: t.ink[3], formatter: '{value}%' }, splitLine: { show: false } },
      series: [{
        type: 'bar', barWidth: '54%',
        data: data.map((d, i) => ({ value: d.pct, itemStyle: { color: t.amber(data.length > 1 ? i / (data.length - 1) : 0), borderRadius: [4, 4, 0, 0] } })),
        label: { show: true, position: 'top', fontSize: 10, fontWeight: 700, color: t.ink[1], formatter: '{c}%' },
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
