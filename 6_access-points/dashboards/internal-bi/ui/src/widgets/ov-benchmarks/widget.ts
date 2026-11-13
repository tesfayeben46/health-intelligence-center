/**
 * System benchmarks: 4 high-level "how big is the system" stat cards (Overview Headline).
 * Light cards matching the existing design system (tokens/type/sprite icons); span the full
 * Headline row (4-across), each with a distinct on-palette accent icon. The ov-benchmarks
 * fetcher fills them from the facility registry and workforce marts.
 *
 * Data contract, one row per card:
 *   icon:sprite-id · value:str · label:str · note:str
 * reactsTo: geography · period.
 */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';

// Country-neutral empty-state: shown only when fetchData() returns nothing. Values
// are 'n/a' instead of a specific country's figures, and labels avoid
// Ethiopia-only terms, so a profile with no benchmark data never shows Ethiopian numbers.
const DEFAULTS = [
  { icon: 'i-hospital', value: 'n/a', label: 'Health facilities', note: 'On the master facility registry' },
  { icon: 'i-users', value: 'n/a', label: 'Population', note: 'Catchment served' },
  { icon: 'i-map', value: 'n/a', label: 'Regions', note: 'Administrative areas' },
  { icon: 'i-map', value: 'n/a', label: 'Districts covered', note: 'Districts with a facility' },
];

const def: WidgetDef = {
  id: 'ov-benchmarks',
  reactsTo: ['geography'],
  frameless: true,   // tiles are the cards: host draws no wrapping card
  async render(ctx: WidgetCtx) {
    const rows = await ctx.fetchData();
    const cards = rows && rows.length ? rows : DEFAULTS;
    const t = ctx.theme;

    // Distinct on-palette accent per card: introduces color into the four icons.
    const accents = [t.status.info, t.status.good, t.status.watch, t.navy['600']];
    const tint = (hex: string, a: number) => {
      const m = /^#?([0-9a-f]{6})$/i.exec(hex); if (!m) return hex;
      const n = parseInt(m[1], 16); return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
    };

    ctx.el.innerHTML = `
      <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1rem;height:100%">
        ${cards.map((c: any, i: number) => {
          const col = accents[i % accents.length];
          // The gateway ships icon tokens without the sprite's `i-` prefix (e.g. "building",
          // "check-circle"); the sprite ids are `i-building` etc. Normalise so `<use>` resolves,
          // falling back to this card's positional default when the row omits an icon.
          const raw = String(c.icon ?? '').trim();
          const iconId = raw ? (raw.startsWith('i-') ? raw : `i-${raw}`) : DEFAULTS[i % DEFAULTS.length].icon;
          return `
          <div style="background:${t.paper};border-radius:16px;padding:1.1rem 1.2rem;box-shadow:0 1px 3px rgba(15,23,42,.06),0 6px 20px rgba(15,23,42,.04);display:flex;flex-direction:column;gap:.35rem;min-width:0">
            <div style="display:flex;align-items:center;gap:.55rem">
              <span style="display:inline-grid;place-items:center;width:32px;height:32px;border-radius:8px;background:${tint(col, 0.12)};color:${col};flex-shrink:0">
                <svg class="ico" aria-hidden="true"><use href="#${iconId}"/></svg></span>
              <span style="font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:${t.ink[3]}">${c.label ?? ''}</span>
            </div>
            <div style="font-size:1.9rem;font-weight:800;letter-spacing:-.03em;color:${t.ink[0]};font-variant-numeric:tabular-nums;line-height:1;margin-top:.1rem">${c.value ?? ''}</div>
            <div style="font-size:.72rem;color:${t.ink[3]}">${c.note ?? ''}</div>
          </div>`; }).join('')}
      </div>`;
    return () => { /* pure DOM */ };
  },
};
export default def;
