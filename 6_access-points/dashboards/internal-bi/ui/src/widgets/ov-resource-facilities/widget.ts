/** Overview · facility coverage gaps: reuses the rm-hotspots fetcher/mart. */
import type { WidgetDef } from '../../runtime/types';
import { paginatedTable } from '../_shared/charts';
import { regionName } from '../_shared/reference';

const def: WidgetDef = {
  id: 'ov-resource-facilities',
  reactsTo: ['geography'],
  async render(ctx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No facility data for the current scope.'); return; }
    const shaped = rows.slice(0, 6).map(r => ({
      region: regionName(r.region_id),
      facilities: Number(r.n_fac ?? 0),
      hc: Number(r.hc_like ?? 0),
      hospitals: Number(r.hospitals ?? 0),
    }));
    return paginatedTable(ctx.el, shaped, [
      { id: 'region', label: 'Region' },
      { id: 'facilities', label: 'Facilities' },
      { id: 'hc', label: 'HC-like' },
      { id: 'hospitals', label: 'Hospitals' },
    ], ctx.theme, { pageSize: 6 });
  },
};
export default def;
