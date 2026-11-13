/** Overview · supply status digest: reuses the rm-hero fetcher/mart. */
import type { WidgetDef } from '../../runtime/types';
import { kpiStrip } from '../_shared/charts';

const def: WidgetDef = {
  id: 'ov-resource-status',
  frameless: true,   // the KPI tiles are the cards: no wrapping card
  async render(ctx) {
    const r = (await ctx.fetchData())?.[0];
    if (!r) { ctx.empty('No supply data available.'); return; }
    const so = Number(r.stockouts ?? 0), total = Number(r.total ?? 0), mos = Number(r.mos ?? 0);
    return kpiStrip(ctx.el, [
      { label: 'Commodities stocked out', value: total ? `${so} / ${total}` : String(so), status: so >= total * 0.15 ? 'alert' : so > 0 ? 'watch' : 'good', note: 'At the latest month' },
      { label: 'Units wasted', value: Number(r.wastage_units ?? 0).toLocaleString(), note: 'Expiry / damage' },
      { label: 'Avg months of stock', value: mos.toFixed(1), status: mos >= 3 ? 'good' : 'watch', note: 'Across commodities' },
    ], ctx.theme, 3);
  },
};
export default def;
