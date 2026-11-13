/**
 * Stockout Impact on Service (Resource page): estimated service loss during
 * out-of-stock periods, from joining Supply to DHIS2 service volume.
 * estimated_missed_services = expected - observed during the out-of-stock window;
 * service_disruption = missed/expected. Attribution is correlational, so the
 * window is shown. Source: bi_supply + DHIS2 service volume.
 *
 * Data contract: commodity · region · days_out_ytd:int ·
 *   facilities_affected:int · estimated_missed_services:int · service_disruption_pct:num ·
 *   trend:'up'|'flat'|'down'. reactsTo: geography · period.
 */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { regionName } from '../_shared/reference';

const def: WidgetDef = {
  id: 'rm-stockout-impact',
  reactsTo: ['geography', 'period'],
  async render(ctx: WidgetCtx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No stockouts in the last 12 months for this scope. No estimated service impact.'); return; }
    const t = ctx.theme;
    const maxD = Math.max(6, ...rows.map(r => Number(r.service_disruption_pct ?? 0)));
    const trend = (v: string): [string, string] =>
      v === 'up' ? ['Rising', t.status.alert] : v === 'down' ? ['Falling', t.status.good] : ['Steady', t.ink[3]];
    const head = ['Commodity', 'Region', 'Days out (YTD)', 'Facilities affected', 'Est. missed services', 'Service disruption', 'Trend'];
    const body = rows.map(r => {
      const disr = Number(r.service_disruption_pct ?? 0);
      const [arrow, col] = trend(String(r.trend ?? 'flat'));
      return `<tr>
        <td style="font-weight:600;color:${t.ink[0]}">${r.commodity ?? '-'}</td>
        <td style="color:${t.ink[2]}">${regionName(String(r.region ?? ''))}</td>
        <td class="num" style="font-variant-numeric:tabular-nums">${Number(r.days_out_ytd ?? 0)}</td>
        <td class="num" style="font-variant-numeric:tabular-nums">${Number(r.facilities_affected ?? 0).toLocaleString()}</td>
        <td class="num" style="font-variant-numeric:tabular-nums">${Number(r.estimated_missed_services ?? 0).toLocaleString()}</td>
        <td><div style="display:flex;align-items:center;gap:.5rem;min-width:120px">
          <span style="font-variant-numeric:tabular-nums;min-width:34px">${disr.toFixed(1)}%</span>
          <span style="flex:1;height:6px;border-radius:3px;background:${t.navy['50']};overflow:hidden"><span style="display:block;height:100%;width:${Math.round((disr / maxD) * 100)}%;background:${t.status.alert}"></span></span>
        </div></td>
        <td style="color:${col};font-weight:700">${arrow}</td>
      </tr>`;
    }).join('');
    ctx.el.innerHTML = `
      <p style="font-size:.82rem;color:${t.ink[2]};margin:0 0 .5rem">Estimated service loss during out-of-stock periods</p>
      <div class="scrollable"><table class="dash"><thead><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>`;
    return () => { /* pure DOM */ };
  },
};
export default def;
