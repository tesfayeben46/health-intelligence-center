/**
 * System Trust · Facility Mapping Coverage: Overview trust card 2 of 4.
 * facility_mapping = locations_resolved_to_mfr/total_source_locations*100.
 * Data contract, single row:
 *   value:int(%) · suffix:str · note:str(target) · delta_text:str · delta_good:bool · spark:int[]
 * reactsTo: geography · programme · period.
 */
import type { WidgetDef } from '../../runtime/types';
import { statCard } from '../_shared/charts';

const def: WidgetDef = {
  id: 'ov-trust-mapping',
  reactsTo: ['geography'],
  async render(ctx) {
    const r = (await ctx.fetchData())?.[0];
    if (!r) { ctx.empty('No facility mapping coverage for the selected filters.'); return; }
    return statCard(ctx.el, {
      subtitle: 'Locations resolved to an MFR id',
      value: `${Number(r.value ?? 0)}${r.suffix ?? ''}`,
      note: r.note ?? '',
      delta: r.delta_text ?? '',
      deltaGood: r.delta_good !== false,
    }, ctx.theme);
  },
};
export default def;
