/** Overview · top active outbreaks: reuses the dz-outbreaks-table fetcher. */
import type { WidgetDef } from '../../runtime/types';
import { paginatedTable } from '../_shared/charts';
import { regionName } from '../_shared/reference';

const def: WidgetDef = {
  id: 'ov-disease-outbreaks',
  reactsTo: ['geography'],
  async render(ctx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No active outbreaks in the current scope.'); return; }
    const shaped = rows.slice(0, 6).map(r => ({
      disease: r.disease_label ?? r.disease_code ?? '-',
      region: regionName(r.region_id),
      cases: Number(r.cases_total ?? 0),
      status: String(r.status ?? '-'),
    }));
    return paginatedTable(ctx.el, shaped, [
      { id: 'disease', label: 'Disease' },
      { id: 'region', label: 'Region' },
      { id: 'cases', label: 'Cases' },
      { id: 'status', label: 'Status', format: 'pill' },
    ], ctx.theme, { pageSize: 6 });
  },
};
export default def;
