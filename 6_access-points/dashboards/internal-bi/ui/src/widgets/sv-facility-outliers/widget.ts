/**
 * Per-Facility Outliers (Service page).
 * Facility indicators outside an expected, indicator-specific threshold; the
 * expected range is shown on each row.
 * Data contract: facility · indicator · value:str · expected:str ·
 *   severity ('Alert'|'Watch'). reactsTo: geography · programme · period.
 */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';

const SEV_PILL: Record<string, string> = { Alert: 'alert', Watch: 'watch' };

const def: WidgetDef = {
  id: 'sv-facility-outliers',
  reactsTo: ['geography', 'programme', 'period'],
  async render(ctx: WidgetCtx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No facility outliers for the selected filters.'); return; }
    const t = ctx.theme;
    const head = ['Facility', 'Indicator', 'Value', 'Expected', 'Severity'];
    const body = rows.map(r => {
      const sev = String(r.severity ?? '');
      return `<tr>
        <td style="font-weight:600;color:${t.navy['700']}">${r.facility ?? '-'}</td>
        <td>${r.indicator ?? '-'}</td>
        <td class="num" style="font-variant-numeric:tabular-nums;font-weight:600">${r.value ?? '-'}</td>
        <td style="color:${t.ink[3]}">${r.expected ?? '-'}</td>
        <td><span class="pill ${SEV_PILL[sev] || 'neutral'}">${sev || '-'}</span></td>
      </tr>`;
    }).join('');
    ctx.el.innerHTML = `
      <p class="widget-lead" style="color:var(--ink-3);font-size:.8rem;margin:0 0 .6rem">Facility indicators that fall outside their expected range, with the expected band shown per row so outliers are judged in context.</p>
      <div class="scrollable"><table class="dash"><thead><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>`;
    return () => { /* pure DOM */ };
  },
};
export default def;
