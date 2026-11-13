/**
 * Active Outbreaks hero: dark photographic KPI card (Overview Headline).
 * Styling lives in base.css `.ov-outbreak-card`. Imperative render with the project SVG
 * sprite icons (i-users / i-trend-up / i-pin / i-shield-check).
 *
 * Data contract (the layout is fixed whatever the values):
 *   eyebrow:str · headline:str · metrics: [icon:sprite-id, value:str, label:str][]  (\n wraps label)
 * reactsTo: geography · period.
 */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';

// Country-neutral empty-state (shown only when fetchData() is empty): em-dashes
// rather than a specific country's outbreak figures or place names.
const DEFAULTS = {
  eyebrow: 'Active outbreaks, last 30 days',
  headline: 'n/a',
  metrics: [
    { icon: 'i-users', value: 'n/a', label: 'new cases\nthis week' },
    { icon: 'i-trend-up', value: 'n/a', label: 'CFR' },
    { icon: 'i-pin', value: 'n/a', label: 'top hotspot' },
    { icon: 'i-shield-check', value: 'n/a', label: 'P(alarm)' },
  ] as { icon: string; value: string; label: string }[],
};

const def: WidgetDef = {
  id: 'ov-outbreak-hero',
  reactsTo: ['geography'],
  async render(ctx: WidgetCtx) {
    const r = (await ctx.fetchData())?.[0];
    const eyebrow = String(r?.eyebrow ?? DEFAULTS.eyebrow);
    const headline = String(r?.headline ?? DEFAULTS.headline);
    const metrics = Array.isArray(r?.metrics) && r!.metrics.length ? r!.metrics : DEFAULTS.metrics;

    // Full-bleed: strip the host's white card chrome so the dark card owns the whole tile.
    const card = ctx.el.closest('.widget-card') as HTMLElement | null;
    if (card) { card.style.padding = '0'; card.style.background = 'transparent'; card.style.boxShadow = 'none'; card.style.overflow = 'visible'; }

    ctx.el.innerHTML = `
      <div class="ov-outbreak-card">
        <div>
          <div class="eyebrow">${eyebrow}</div>
          <div class="big-number">${headline}</div>
        </div>
        <div class="metrics">
          ${metrics.map((m: any) => {
            // Normalise data-supplied icon tokens to sprite ids (gateway ships them without the
            // `i-` prefix, e.g. "users" to "i-users") so `<use>` resolves instead of drawing blank.
            const raw = String(m.icon ?? '').trim();
            const iconId = raw ? (raw.startsWith('i-') ? raw : `i-${raw}`) : 'i-info';
            return `
            <div class="metric">
              <svg aria-hidden="true"><use href="#${iconId}"/></svg>
              <div>
                <div class="val">${m.value}</div>
                <div class="lbl">${String(m.label ?? '')}</div>
              </div>
            </div>`; }).join('')}
        </div>
      </div>`;
    return () => { /* pure DOM */ };
  },
};
export default def;
