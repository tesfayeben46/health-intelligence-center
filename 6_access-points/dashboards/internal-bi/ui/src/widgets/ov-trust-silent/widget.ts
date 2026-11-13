/**
 * System Trust · Active-but-Silent Facilities: Overview trust card 3 of 4.
 * Count of active MFR facilities with no recent DHIS2 report and no supply/EMR
 * signal in the window. Fewer is better, so a downward delta is coloured good
 * (trend colour follows meaning, not arrow direction).
 * Data contract, single row:
 *   value:int · suffix:str · note:str · delta_text:str · delta_good:bool · spark:int[]
 * reactsTo: geography · programme · period.
 */
import type { WidgetDef } from '../../runtime/types';
import { statCard } from '../_shared/charts';

const def: WidgetDef = {
  id: 'ov-trust-silent',
  reactsTo: ['geography'],
  async render(ctx) {
    const r = (await ctx.fetchData())?.[0];
    if (!r) { ctx.empty('No active-but-silent facility data for the selected filters.'); return; }
    return statCard(ctx.el, {
      subtitle: 'Facilities silent 30+ days',
      value: Number(r.value ?? 0).toLocaleString(),
      note: r.note ?? '',
      delta: r.delta_text ?? '',
      deltaGood: r.delta_good !== false,
    }, ctx.theme);
  },
};
export default def;
