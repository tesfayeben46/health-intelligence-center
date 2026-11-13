/** Region by commodity months-on-hand heatmap: bi_supply.mart_supply_region_stock
 *  (HCMIS EPSS hub to region_id). Low stock = red, healthy = green. */
import type { WidgetDef } from '../../runtime/types';
import { heatmap, STOCK_RAMP } from '../_shared/charts';
import { regionName } from '../_shared/reference';

const def: WidgetDef = {
  id: 'rm-stock-heatmap',
  reactsTo: ['geography', 'commodity'],
  async render(ctx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No regional stock data for the current scope.'); return; }
    const regions = [...new Set(rows.map(r => String(r.region_id)))].sort();
    const byCommodity: Record<string, Record<string, number>> = {};
    for (const r of rows) (byCommodity[String(r.commodity_name)] ||= {})[String(r.region_id)] = Number(r.mos ?? 0);
    // No display cap: the heatmap scrolls + paginates, so every commodity is reachable.
    const commodities = Object.keys(byCommodity).sort();
    const heatRows = commodities.map(c => ({ name: c, probs: regions.map(reg => (byCommodity[c][reg] ?? null)) }));
    ctx.el.style.display = 'flex';
    ctx.el.style.flexDirection = 'column';
    ctx.el.innerHTML = `<p class="widget-lead" style="color:var(--ink-3);font-size:.8rem;margin:0 0 .6rem">Months of stock on hand for each commodity across every region: red cells are running low, green cells are healthy. Scroll for more commodities.</p><div class="rm-hm-mount" style="flex:1;min-height:0"></div>`;
    const mount = ctx.el.querySelector('.rm-hm-mount') as HTMLElement;
    return heatmap(mount, heatRows, regions.map(regionName), ctx.theme, {
      max: 6, ramp: STOCK_RAMP, fmt: (v: number) => `${v.toFixed(1)}mo`, label: 'Stock',
      rowTitle: 'Commodity', colTitle: 'Region', rowNoun: 'commodities',
    });
  },
};
export default def;
