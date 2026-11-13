/** National snapshot: whole-system headline numbers (cross-mart, national). */
import type { WidgetDef } from '../../runtime/types';
import { kpiStrip, type KpiCell } from '../_shared/charts';
import { planLabel } from '../_shared/reference';

const def: WidgetDef = {
  id: 'ov-national-kpis',
  frameless: true,   // the KPI tiles are the cards: no wrapping card
  async render(ctx) {
    const r = (await ctx.fetchData())?.[0];
    if (!r) { ctx.empty('No national data available.'); return; }
    const outbreaks = Number(r.active_outbreaks ?? 0), avail = Number(r.supply_avail_pct ?? 0);
    const cells: KpiCell[] = [
      { label: 'Active outbreaks', value: String(outbreaks), status: outbreaks >= 4 ? 'alert' : outbreaks >= 1 ? 'watch' : 'good', note: 'Declared nationwide' },
      { label: 'Regions reporting', value: String(r.regions_reporting ?? 0), note: 'Of 14 regions' },
      { label: 'Health facilities', value: Number(r.facilities ?? 0).toLocaleString(), note: 'On the master registry' },
      { label: 'Commodity availability', value: `${Math.round(avail)}%`, status: avail >= 80 ? 'good' : avail >= 50 ? 'watch' : 'alert', note: '>= 3 months of stock' },
      { label: 'Avg service coverage', value: `${Number(r.avg_coverage ?? 0).toFixed(0)}%`, note: planLabel('indicators') },
    ];
    return kpiStrip(ctx.el, cells, ctx.theme, 5);
  },
};
export default def;
