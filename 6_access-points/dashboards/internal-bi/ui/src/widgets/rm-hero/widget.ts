/**
 * Stockout Watch: headline hero (top of the Resource page).
 * The AMC-based stockout count, plus a secondary read (services exposed behind the
 * stockouts) so the headline shows impact, not just inventory. No population-impact
 * claim without a population denominator: exposure is services via DHIS2 volume.
 * Source: bi_supply.mart_resource_allocation/stockout_forecast (+ DHIS2 service volume + MFR).
 *
 * Data contract, single row:
 *   commodities_at_risk:int · commodity_region_pairs:int · already_stockout:int ·
 *   value_at_risk_etb:num · services_exposed:int · estimated_service_disruption:int ·
 *   urgent_items:str ("name|days;name|days;…")
 * reactsTo: geography · period · commodity.
 */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';

const def: WidgetDef = {
  id: 'rm-hero',
  reactsTo: ['geography', 'period', 'commodity'],
  async render(ctx: WidgetCtx) {
    const r = (await ctx.fetchData())?.[0];
    if (!r) { ctx.empty('No stockout headline data for the selected filters.'); return; }
    const t = ctx.theme;
    // Shared card DNA: copied from the Overview Headline stat cards (ov-benchmarks) so these read
    // as the same, considered system: paper surface, soft shadow, radius 16, even 1rem 1.1rem padding.
    // Borderless (cards never have borders); the soft shadow carries the elevation.
    const tint = (hex: string, a: number) => {
      const m = /^#?([0-9a-f]{6})$/i.exec(hex); if (!m) return hex;
      const n = parseInt(m[1], 16); return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
    };
    const CARD = `background:${t.paper};border-radius:16px;padding:1rem 1.1rem;box-shadow:var(--shadow-1);display:flex;flex-direction:column;gap:.35rem;min-width:0`;
    const chip = (icon: string, col: string) => `<span style="display:inline-grid;place-items:center;width:32px;height:32px;border-radius:8px;background:${tint(col, 0.12)};color:${col};flex-shrink:0"><svg class="ico" aria-hidden="true"><use href="#${icon}"/></svg></span>`;
    const label = (s: string) => `<span style="font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:${t.ink[3]}">${s}</span>`;
    // A lower impact card: vertically centered in its (tall) grid row, no bottom-pinning.
    const stat = (icon: string, col: string, lbl: string, val: string, sub: string) => `
      <div style="${CARD};justify-content:center">
        <div style="display:flex;align-items:center;gap:.55rem">${chip(icon, col)}${label(lbl)}</div>
        <div style="font-size:1.9rem;font-weight:800;letter-spacing:-.03em;color:${t.ink[0]};font-variant-numeric:tabular-nums;line-height:1;margin-top:.1rem">${val}</div>
        <div style="font-size:.72rem;color:${t.ink[3]}">${sub}</div>
      </div>`;

    ctx.el.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;grid-template-rows:auto 1fr;gap:1rem;height:100%">
        <div style="${CARD};grid-column:1 / -1">
          <div style="display:flex;align-items:center;gap:.55rem">${chip('i-package', t.status.watch)}${label('Commodities at risk of stockout')}</div>
          <div style="display:flex;align-items:baseline;gap:.55rem;margin-top:.1rem">
            <div style="font-size:2.2rem;font-weight:800;letter-spacing:-.03em;color:${t.ink[0]};font-variant-numeric:tabular-nums;line-height:1">${Number(r.commodities_at_risk ?? 0)}</div>
            <div style="font-size:.72rem;color:${t.ink[3]}">of ${Number(r.commodity_region_pairs ?? 0)} commodity-region pairs</div>
          </div>
          <span style="display:inline-flex;align-items:center;gap:.3rem;align-self:flex-start;padding:.15rem .5rem;border-radius:999px;background:${tint(t.status.alert, 0.10)};color:${t.status.alert};font-size:.7rem;font-weight:700;margin-top:.25rem">
            <span style="width:.4rem;height:.4rem;border-radius:50%;background:currentColor"></span>${Number(r.already_stockout ?? 0)} already in stockout</span>
        </div>
        ${stat('i-users', t.status.info, 'Services exposed', Number(r.services_exposed ?? 0).toLocaleString(), 'behind at-risk commodities')}
        ${stat('i-alert', t.status.alert, 'Est. service disruption', Number(r.estimated_service_disruption ?? 0).toLocaleString(), 'estimated missed services')}
      </div>`;
    return () => { /* pure DOM */ };
  },
};
export default def;
