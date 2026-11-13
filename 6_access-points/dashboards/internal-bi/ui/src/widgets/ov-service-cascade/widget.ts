/** Overview · operational quality digest: reuses the sv-compliance fetcher. */
import type { WidgetDef } from '../../runtime/types';
import { lollipop } from '../_shared/charts';

const def: WidgetDef = {
  id: 'ov-service-cascade',
  reactsTo: ['geography', 'programme'],
  async render(ctx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No coverage data for the current scope.'); return; }
    const data = rows
      .map(r => ({ name: r.indicator ?? r.name ?? '-', value: Number(r.value ?? 0) }))
      .filter(d => d.value > 0)
      .slice(0, 8);
    if (!data.length) { ctx.empty('No coverage data for the current scope.'); return; }
    return lollipop(ctx.el, data, ctx.theme, { pct: true, xMax: 100 });
  },
};
export default def;
