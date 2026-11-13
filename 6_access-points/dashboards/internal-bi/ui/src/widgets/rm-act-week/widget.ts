/** Act this week: bi_supply.mart_supply_outbreak_response. THE warehouse
 *  join: IDSR outbreak pressure (4-week cases + trend) joined with response-commodity
 *  stock (catchment-blended mos), ranked by act_score. supply_status
 *  'no_countermeasure' is a real absence (no tracked commodity answers
 *  that disease), never rendered as missing data. */
import type { WidgetDef } from '../../runtime/types';
import { paginatedTable } from '../_shared/charts';
import { regionName } from '../_shared/reference';

const def: WidgetDef = {
  id: 'rm-act-week',
  reactsTo: ['geography'],
  async render(ctx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No active outbreak signal in the current scope.'); return; }
    const shaped = rows.map(r => ({
      region: regionName(r.region_id),
      disease: r.disease_label ?? r.disease_code ?? '-',
      cases: Number(r.cases_4w ?? 0),
      trend: r.trend_ratio == null ? 'n/a'
        : Number(r.trend_ratio) > 1.05 ? `up ${Number(r.trend_ratio).toFixed(2)}x`
        : Number(r.trend_ratio) < 0.95 ? `down ${Number(r.trend_ratio).toFixed(2)}x`
        : `steady ${Number(r.trend_ratio).toFixed(2)}x`,
      commodity: r.commodity_name ?? 'No tracked countermeasure',
      role: r.commodity_role ?? 'n/a',
      mos: r.mos == null ? 'n/a' : Number(r.mos).toFixed(1),
      status: String(r.supply_status ?? '-').replace(/_/g, ' '),
    }));
    return paginatedTable(ctx.el, shaped, [
      { id: 'region', label: 'Region' },
      { id: 'disease', label: 'Outbreak (4w cases)' },
      { id: 'cases', label: 'Cases 4w' },
      { id: 'trend', label: 'Trend' },
      { id: 'commodity', label: 'Response commodity' },
      { id: 'role', label: 'Role', format: 'pill' },
      { id: 'mos', label: 'MOS' },
      { id: 'status', label: 'Supply', format: 'pill' },
    ], ctx.theme, { search: true, sort: false, pageSize: 8 });
  },
};
export default def;
