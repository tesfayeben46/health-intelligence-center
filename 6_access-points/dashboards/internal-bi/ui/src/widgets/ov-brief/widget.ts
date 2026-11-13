/** Decision brief: the policy-maker's morning read, one cross-domain brief only
 *  the warehouse can write. Each line comes from a different mart family
 *  (outbreak and supply, surveillance, supply, reporting trust, service cascades).
 *  Rendered as a table (area · signal · priority) with minimal colour: one
 *  subtle severity pill, no loud highlights. */
import type { WidgetDef } from '../../runtime/types';

const esc = (s: unknown) =>
  String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));

const TONE: Record<string, string> = {
  stockout: 'alert', critical: 'alert', tight: 'watch', ok: 'good', info: 'info',
};
const AREA: Record<string, string> = {
  act: 'Act now', surveillance: 'Surveillance', supply: 'Supply',
  trust: 'Data trust', service: 'Service',
};

const def: WidgetDef = {
  id: 'ov-brief',
  reactsTo: [],
  async render(ctx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No brief available.'); return; }
    const t = ctx.theme;
    const body = rows.map((r: any) => {
      const sev = String(r.severity ?? 'info');
      const area = AREA[String(r.kind)] ?? 'n/a';
      return `<tr>
        <td style="white-space:nowrap;color:${t.ink[2]};font-weight:600;font-size:.78rem;text-transform:uppercase;letter-spacing:.03em">${esc(area)}</td>
        <td style="color:${t.ink[0]};line-height:1.45">${esc(r.headline ?? '')}</td>
        <td style="text-align:right;white-space:nowrap"><span class="pill ${TONE[sev] ?? 'neutral'}">${esc(sev)}</span></td>
      </tr>`;
    }).join('');
    ctx.el.innerHTML = `
      <div class="scrollable" style="overflow:auto">
        <table class="dash" style="margin:0;width:100%">
          <thead><tr>
            <th style="text-align:left">Area</th>
            <th style="text-align:left">Decision signal</th>
            <th style="text-align:right">Priority</th>
          </tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>`;
  },
};
export default def;
