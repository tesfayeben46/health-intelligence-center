/**
 * System Trust · Reporting Completeness: Overview trust card 1 of 4.
 * reporting_completeness = reports_received/reports_expected*100.
 * Data contract, single row:
 *   value:int(%) · suffix:str · note:str(target) · delta_text:str · delta_good:bool · spark:int[]
 * reactsTo: geography · programme · period.
 */
import type { WidgetDef } from '../../runtime/types';
import { statCard } from '../_shared/charts';

const def: WidgetDef = {
  id: 'ov-trust-completeness',
  reactsTo: ['geography'],
  async render(ctx) {
    const r = (await ctx.fetchData())?.[0];
    if (!r) { ctx.empty('No reporting completeness for the selected filters.'); return; }
    return statCard(ctx.el, {
      subtitle: '7-day reporting completeness',
      value: `${Number(r.value ?? 0)}${r.suffix ?? ''}`,
      note: r.note ?? '',
      delta: r.delta_text ?? '',
      deltaGood: r.delta_good !== false,
    }, ctx.theme);
  },
};
export default def;
