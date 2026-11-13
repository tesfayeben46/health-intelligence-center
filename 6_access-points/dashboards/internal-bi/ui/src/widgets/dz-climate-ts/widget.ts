/** Cases vs climate drivers, from bi_idsr.mart_idsr_climate_drivers (week by region).
 *  Aggregated to weekly totals across the scope; drivers averaged. */
import type { WidgetDef } from '../../runtime/types';
import { dualAxisBarLine } from '../_shared/charts';
import { isClimateSensitive, periodWeeks } from '../_shared/reference';

const def: WidgetDef = {
  id: 'dz-climate-ts',
  reactsTo: ['geography', 'programme', 'period'],
  async render(ctx) {
    if (!isClimateSensitive(ctx.filters.programme)) {
      ctx.empty('Climate drivers apply to climate-sensitive programmes and diseases.'); return;
    }
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No climate-driver data for the current scope.'); return; }
    const byWeek: Record<string, { start: string; cases: number; rain: number[]; temp: number[]; hum: number[] }> = {};
    for (const r of rows) {
      const wk = String(r.iso_week ?? r.week_start ?? '');
      const b = (byWeek[wk] ||= { start: String(r.week_start ?? wk), cases: 0, rain: [], temp: [], hum: [] });
      b.cases += Number(r.cases ?? 0);
      if (r.rainfall_mm != null) b.rain.push(+r.rainfall_mm);
      if (r.temp_celsius != null) b.temp.push(+r.temp_celsius);
      if (r.humidity_pct != null) b.hum.push(+r.humidity_pct);
    }
    const N = periodWeeks(ctx.filters.period);
    const weeks = Object.keys(byWeek).sort((a, b) => byWeek[a].start.localeCompare(byWeek[b].start)).slice(-N);
    if (!weeks.length) { ctx.empty('No climate-driver data for the current scope.'); return; }
    const avg = (a: number[]) => (a.length ? +(a.reduce((x, y) => x + y, 0) / a.length).toFixed(1) : null);
    const t = ctx.theme;
    // Flex column + fill chart: the chart grows to the card's height so a taller
    // neighbour (the lag-correlation chart) never leaves a dead zone below.
    ctx.el.style.display = 'flex';
    ctx.el.style.flexDirection = 'column';
    ctx.el.innerHTML = `
      <div class="cl-mount" style="flex:1;min-height:280px"></div>`;
    const mount = ctx.el.querySelector('.cl-mount') as HTMLElement;
    return dualAxisBarLine(mount, {
      weeks: weeks.map(w => w.replace(/^\d{4}/, '')),   // "2026W20" to "W20" (less stacked)
      cases: weeks.map(w => byWeek[w].cases),
      lines: [
        { name: 'Rainfall (mm)', data: weeks.map(w => avg(byWeek[w].rain) as number), color: t.navy['500'] },
        { name: 'Temp (°C)', data: weeks.map(w => avg(byWeek[w].temp) as number), color: t.status.alert, dashed: true },
        { name: 'Humidity (%)', data: weeks.map(w => avg(byWeek[w].hum) as number), color: t.ink[3] },
      ],
    }, t, { fill: true });
  },
};
export default def;
