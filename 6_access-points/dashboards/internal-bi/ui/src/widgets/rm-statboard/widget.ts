/**
 * Commodity status cards + Facility Integrity (Resource page).
 * Four supply-readiness stat cards (Drugs & Supplies, Availability, Stockout Rate,
 * Inventory Value at Risk) plus a Facility Integrity strip (mapping coverage,
 * unmapped, active-but-silent). Labels make clear that silent is not closed and
 * unmapped is not nonexistent.
 * Source: bi_supply.mart_resource_allocation + MFR/DQ (integrity).
 *
 * Data contract, rows keyed by `kind`:
 *   kind='stat': label · value:num · unit:'percent'|'etb-m' · target:str · delta_text:str ·
 *                delta_good:bool · spark:int[]
 *   kind='integrity': label · value:num · unit:'percent'|'count'
 * reactsTo: geography · period · commodity.
 */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { sparklineSvg } from '../_shared/charts';
import { moneyM } from '../_shared/reference';

const fmt = (v: number, unit: string) =>
  unit === 'percent' ? `${v}%` : unit === 'etb-m' ? moneyM(v)
  : unit === 'months' ? `${v} mo` : Number(v).toLocaleString();

const def: WidgetDef = {
  id: 'rm-statboard',
  reactsTo: ['geography', 'period', 'commodity'],
  async render(ctx: WidgetCtx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No resource statboard data for the selected filters.'); return; }
    const t = ctx.theme;
    // Mock contract keys stat tiles kind='stat'; the live fetcher emits one kind
    // per METRIC (availability/mos/stockout/…). Treat every non-integrity row as
    // a stat tile so both vocabularies render.
    const stats = rows.filter(r => r.kind !== 'integrity');
    const integ = rows.filter(r => r.kind === 'integrity');

    const card = (r: any) => {
      const spark = Array.isArray(r.spark) ? r.spark.map(Number) : null;
      const deltaColor = r.delta_good === false ? t.status.alert : t.status.good;
      return `<div style="background:${t.paper};border-radius:12px;padding:.7rem .8rem;box-shadow:var(--shadow-1);display:flex;flex-direction:column;gap:.25rem">
        <div style="font-size:.74rem;font-weight:600;color:${t.ink[2]}">${r.label}</div>
        <div style="display:flex;align-items:flex-end;justify-content:space-between;gap:.4rem">
          <div style="font-size:1.5rem;font-weight:800;letter-spacing:-.03em;color:${t.ink[0]};font-variant-numeric:tabular-nums;line-height:1">${fmt(Number(r.value ?? 0), String(r.unit ?? ''))}</div>
          ${spark ? sparklineSvg(spark, t, { w: 76, h: 26 }) : ''}
        </div>
        <div style="font-size:.7rem;color:${t.ink[4]}">${r.target ?? ''}</div>
        ${r.delta_text ? `<div style="font-size:.72rem;font-weight:600;color:${deltaColor}">${r.delta_text}</div>` : ''}
      </div>`;
    };

    ctx.el.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.7rem">${stats.map(card).join('')}</div>
      ${integ.length ? `<div style="margin-top:.8rem;padding-top:.7rem;border-top:1px solid ${t.line}">
        <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:${t.ink[3]};margin-bottom:.5rem">Facility Integrity</div>
        <div style="display:flex;gap:1.4rem">
          ${integ.map(r => `<div>
            <div style="font-size:1.15rem;font-weight:800;color:${t.ink[0]};font-variant-numeric:tabular-nums">${fmt(Number(r.value ?? 0), String(r.unit ?? 'count'))}</div>
            <div style="font-size:.72rem;color:${t.ink[3]}">${r.label}</div></div>`).join('')}
        </div>
      </div>` : ''}`;
    return () => { /* pure DOM */ };
  },
};
export default def;
