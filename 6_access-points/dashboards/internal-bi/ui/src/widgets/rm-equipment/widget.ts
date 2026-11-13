/** Equipment availability (working/broken/missing): bi_facility.mart_equipment_availability.
 *  (MFR FHIR Device resources have no mart yet; empty until it lands.) */
import type { WidgetDef } from '../../runtime/types';
import { stackedBar } from '../_shared/charts';

const def: WidgetDef = {
  id: 'rm-equipment',
  async render(ctx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No equipment data available.'); return; }
    const categories = rows.map(r => String(r.equipment_type ?? '-'));
    return stackedBar(ctx.el, {
      categories,
      series: [
        { name: 'Working', color: '#15865b', data: rows.map(r => Number(r.working ?? 0)) },
        { name: 'Broken', color: '#e3a33a', data: rows.map(r => Number(r.broken ?? 0)) },
        { name: 'Missing', color: '#c0392b', data: rows.map(r => Number(r.missing ?? 0)) },
      ],
    }, ctx.theme);
  },
};
export default def;
