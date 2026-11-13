/**
 * Cold-Chain & Safety (Resource page): cold-chain integrity linked to immunization
 * safety. A supporting card, not an alarm without investigation; an empty source is
 * never rendered as live.
 * Source: bi_supply (cold-chain) + DHIS2 (immunization) + AEFI source.
 *
 * Data contract, single row:
 *   temperature_excursions:int · affected_batches:int · recipients_exposed:int ·
 *   linked_aefi_events:int · batch_notes:str
 * reactsTo: geography · period.
 */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';

const def: WidgetDef = {
  id: 'rm-cold-chain',
  reactsTo: ['geography', 'period'],
  async render(ctx: WidgetCtx) {
    const r = (await ctx.fetchData())?.[0];
    if (!r) { ctx.empty('No cold-chain safety data for the selected filters.'); return; }
    const t = ctx.theme;
    // Shared card DNA: matched to the Overview Headline stat cards (paper surface, soft shadow,
    // radius 16, even padding, colored icon chip on one top row). Four distinct on-palette accents.
    const tint = (hex: string, a: number) => {
      const m = /^#?([0-9a-f]{6})$/i.exec(hex); if (!m) return hex;
      const n = parseInt(m[1], 16); return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
    };
    const tiles = [
      { v: Number(r.temperature_excursions ?? 0), icon: 'i-thermo', col: t.status.watch, label: 'Temperature excursions', sub: 'logged this quarter' },
      { v: Number(r.affected_batches ?? 0), icon: 'i-package', col: t.navy['600'], label: 'Affected batches', sub: String(r.batch_notes ?? '') },
      { v: Number(r.recipients_exposed ?? 0), icon: 'i-users', col: t.status.info, label: 'Recipients exposed', sub: 'doses, FHIR-linked' },
      { v: Number(r.linked_aefi_events ?? 0), icon: 'i-syringe', col: t.status.alert, label: 'Linked AEFI events', sub: 'mild and serious' },
    ];
    ctx.el.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;grid-auto-rows:1fr;gap:1rem;height:100%">
        ${tiles.map(x => `<div style="background:${t.paper};border-radius:16px;padding:1rem 1.1rem;box-shadow:var(--shadow-1);display:flex;flex-direction:column;justify-content:center;gap:.35rem;min-width:0">
          <div style="display:flex;align-items:center;gap:.55rem">
            <span style="display:inline-grid;place-items:center;width:32px;height:32px;border-radius:8px;background:${tint(x.col, 0.12)};color:${x.col};flex-shrink:0"><svg class="ico" aria-hidden="true"><use href="#${x.icon}"/></svg></span>
            <span style="font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:${t.ink[3]}">${x.label}</span>
          </div>
          <div style="font-size:1.9rem;font-weight:800;letter-spacing:-.03em;color:${t.ink[0]};font-variant-numeric:tabular-nums;line-height:1;margin-top:.1rem">${x.v.toLocaleString()}</div>
          <div style="font-size:.72rem;color:${t.ink[3]}">${x.sub}</div></div>`).join('')}
      </div>`;
    return () => { /* pure DOM */ };
  },
};
export default def;
