/**
 * Surveillance signal header: six headline tiles on the Disease page. Each tile
 * carries a reporting-completeness footnote so a low count can be read as low
 * or under-reported; completeness is a trust cue, not a case rate.
 * Source: bi_idsr.mart_idsr_disease_weekly with model output, plus DQ
 * (expected woreda-weeks).
 *
 * Data contract, one row per tile:
 *   tile:str · value:num · unit:'count'|'percent' · subtitle:str · delta_text:str ·
 *   delta_direction:'good'|'watch'|'alert' · footnote_label:str · footnote_value:str · icon:str
 * reactsTo: geography · programme · period.
 */
import type { WidgetDef } from '../../runtime/types';
import { kpiStrip, type KpiCell } from '../_shared/charts';

const def: WidgetDef = {
  id: 'dz-kpi-strip',
  frameless: true,   // the KPI tiles are the cards; no wrapping card
  reactsTo: ['geography', 'programme', 'period'],
  async render(ctx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No surveillance headline data for the selected filters.'); return; }
    const fmt = (v: number, unit: string) =>
      unit === 'percent' ? `${(+v).toFixed(Number.isInteger(+v) ? 0 : 1)}%` : (+v).toLocaleString();
    const cells: KpiCell[] = rows.map(r => {
      const dir = String(r.delta_direction ?? '');
      return {
        label: String(r.tile ?? ''),
        value: String(r.unit) === 'text' ? String(r.value ?? '-') : fmt(Number(r.value ?? 0), String(r.unit ?? 'count')),
        sub: r.subtitle ? String(r.subtitle) : undefined,
        delta: r.delta_text ? String(r.delta_text) : undefined,
        deltaDir: dir === 'alert' ? 'down' : dir === 'good' ? 'up' : undefined,
        foot: (r.footnote_label || r.footnote_value)
          ? `${r.footnote_label ?? ''} ${r.footnote_value ?? ''}`.trim() : undefined,
        status: dir === 'alert' ? 'alert' : dir === 'watch' ? 'watch' : dir === 'good' ? 'good' : 'info',
      };
    });
    return kpiStrip(ctx.el, cells, ctx.theme, 6);
  },
};
export default def;
