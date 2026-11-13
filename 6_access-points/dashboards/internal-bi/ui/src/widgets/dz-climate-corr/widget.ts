/** Rainfall-to-cases lag correlation per disease, from bi_idsr.mart_idsr_climate_drivers.
 *  The mart carries one rainfall correlation and best lag per region by disease;
 *  the widget shows a diverging bar per disease of the mean signed r, annotated
 *  with the median best lag (top 10 by |r|). Reacts to geography and programme;
 *  non-climate-sensitive programmes get an empty state. */
import type { WidgetDef } from '../../runtime/types';
import { divergingBar } from '../_shared/charts';
import { isClimateSensitive } from '../_shared/reference';

const median = (a: number[]): number => {
  if (!a.length) return 0;
  const s = [...a].sort((x, y) => x - y); const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : Math.round((s[m - 1] + s[m]) / 2);
};

const def: WidgetDef = {
  id: 'dz-climate-corr',
  reactsTo: ['geography', 'programme'],
  async render(ctx) {
    if (!isClimateSensitive(ctx.filters.programme)) {
      ctx.empty('Lag correlation applies to climate-sensitive programmes and diseases.'); return;
    }
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No lag-correlation data for the current scope.'); return; }
    const byDisease: Record<string, { rs: number[]; lags: number[] }> = {};
    for (const r of rows) {
      const d = String(r.disease_label ?? r.disease_code ?? '-');
      const b = (byDisease[d] ||= { rs: [], lags: [] });
      if (r.rainfall_lag_correlation != null) b.rs.push(+r.rainfall_lag_correlation);
      if (r.lag_weeks != null) b.lags.push(+r.lag_weeks);
    }
    const data = Object.entries(byDisease)
      .map(([name, v]) => ({
        name,
        value: v.rs.length ? +(v.rs.reduce((a, b) => a + b, 0) / v.rs.length).toFixed(2) : 0,
        note: v.lags.length ? `best lag ${median(v.lags)}w` : '',
      }))
      .filter(d => d.value !== 0)
      .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
      .slice(0, 10);
    if (!data.length) { ctx.empty('No lag-correlation data for the current scope.'); return; }
    const t = ctx.theme;
    ctx.el.innerHTML = `
      <div class="cl-mount"></div>`;
    const mount = ctx.el.querySelector('.cl-mount') as HTMLElement;
    return divergingBar(mount, data, t, { height: 300, barWidth: 20 });
  },
};
export default def;
