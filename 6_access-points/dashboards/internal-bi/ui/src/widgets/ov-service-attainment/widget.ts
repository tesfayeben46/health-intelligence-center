/** Overview · HSTP-II attainment digest: reuses the sv-flagship-table fetcher,
 *  aggregated to headline coverage numbers. */
import type { WidgetDef } from '../../runtime/types';
import { kpiStrip } from '../_shared/charts';
import { planLabel } from '../_shared/reference';

const def: WidgetDef = {
  id: 'ov-service-attainment',
  frameless: true,   // the KPI tiles are the cards: no wrapping card
  reactsTo: ['geography', 'programme'],
  async render(ctx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No coverage data for the current scope.'); return; }
    const vals = rows.map(r => Number(r.value ?? 0));
    const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
    const onTrack = vals.filter(v => v >= 80).length;
    const progs = new Set(rows.map(r => r.programme)).size;
    return kpiStrip(ctx.el, [
      { label: 'Avg coverage', value: `${avg.toFixed(0)}%`, status: avg >= 80 ? 'good' : avg >= 60 ? 'watch' : 'alert', note: `${vals.length} indicators` },
      { label: '>= 80% coverage', value: `${onTrack} / ${vals.length}`, note: 'Indicators on track' },
      { label: 'Programmes tracked', value: String(progs), note: planLabel('flagships') },
    ], ctx.theme, 3);
  },
};
export default def;
