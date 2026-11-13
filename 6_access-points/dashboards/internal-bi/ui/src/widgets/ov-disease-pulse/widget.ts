/** Overview · disease digest KPIs: reuses the dz-kpi-strip fetcher/mart. */
import type { WidgetDef } from '../../runtime/types';
import { kpiStrip } from '../_shared/charts';
import { levelName } from '../_shared/reference';

const def: WidgetDef = {
  id: 'ov-disease-pulse',
  frameless: true,   // the KPI tiles are the cards: no wrapping card
  reactsTo: ['geography', 'programme'],
  async render(ctx) {
    const r = (await ctx.fetchData({ model: 'ears' }))?.[0];
    if (!r) { ctx.empty('No surveillance data for the current scope.'); return; }
    const alarms = Number(r.alarms_now ?? 0);
    return kpiStrip(ctx.el, [
      { label: 'New cases (7d)', value: Number(r.cases_wk ?? 0).toLocaleString(), note: 'This week' },
      { label: 'Active alarms', value: String(alarms), status: alarms >= 4 ? 'alert' : alarms >= 1 ? 'watch' : 'good', note: `By ${levelName('admin3').toLowerCase()} and disease` },
      { label: 'Active diseases', value: String(r.active_diseases ?? 0), note: 'Reporting cases' },
    ], ctx.theme, 3);
  },
};
export default def;
