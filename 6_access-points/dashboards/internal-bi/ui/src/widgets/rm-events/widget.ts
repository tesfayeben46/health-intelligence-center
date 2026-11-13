/** Stockout events: bi_supply.mart_supply_stockout_events (region by commodity
 *  episodes). Empty when stock is healthy. */
import type { WidgetDef } from '../../runtime/types';
import { paginatedTable } from '../_shared/charts';
import { regionName } from '../_shared/reference';

const def: WidgetDef = {
  id: 'rm-events',
  reactsTo: ['geography', 'commodity'],
  async render(ctx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No stockout events in the current scope; stock levels are healthy.'); return; }
    const shaped = rows.map(r => ({
      when: r.episode_start_month ?? '-',
      commodity: r.commodity_name ?? '-',
      region: regionName(r.region_id),
      days: Number(r.days_out ?? 0),
      severity: String(r.severity ?? '-'),
      status: String(r.status ?? '-'),
    }));
    return paginatedTable(ctx.el, shaped, [
      { id: 'when', label: 'Started' },
      { id: 'commodity', label: 'Commodity' },
      { id: 'region', label: 'Region' },
      { id: 'days', label: 'Days out' },
      { id: 'severity', label: 'Severity', format: 'pill' },
      { id: 'status', label: 'Status', format: 'pill' },
    ], ctx.theme, { search: true, sort: true, pageSize: 8 });
  },
};
export default def;
