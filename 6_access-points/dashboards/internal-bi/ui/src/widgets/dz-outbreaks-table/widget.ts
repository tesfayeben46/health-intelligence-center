/**
 * Active outbreaks: episode roster with response readiness (Disease page).
 * The readiness column shows whether RDT/ACT/vaccine stock is available in the
 * affected woredas, so the roster carries its own response context.
 * Source: bi_idsr.mart_idsr_outbreaks plus Supply (readiness) and MFR.
 *
 * Data contract, one row per outbreak episode:
 *   id:str · disease:str · region:str · woreda:str · declared:date · days:int · total:int ·
 *   new_7d:int · cfr_pct:num · status:'Alert'|'Watch' · rrt:str · readiness:'Ready'|'Partial'|'Limited'
 *   (status may also arrive as lowercase active/contained/closed; both vocabularies are mapped).
 * reactsTo: geography.
 *
 * Rendered with the shared paginatedTable (sticky header, fixed-height scroll,
 * search, click-to-sort). The "Subscribe to alerts" control and episode count sit
 * outside the table mount so they survive the table's internal redraws. Status
 * and readiness pills are coloured via TableColumn.tone (active/Alert to alert,
 * contained to good, closed to neutral, Ready to good, Partial to watch, Limited to alert).
 */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import type { TableColumn } from '../_shared/charts';
import { paginatedTable } from '../_shared/charts';
import { regionName } from '../_shared/reference';

// Value to pill token, covering both capitalised and lowercase vocabularies.
const STATUS_TONE: Record<string, string> = {
  Alert: 'alert', Watch: 'watch',
  active: 'alert', Active: 'alert',
  contained: 'good', Contained: 'good',
  closed: 'neutral', Closed: 'neutral',
};
const READY_TONE: Record<string, string> = {
  Ready: 'good', ready: 'good', Partial: 'watch', partial: 'watch', Limited: 'alert', limited: 'alert',
};

const def: WidgetDef = {
  id: 'dz-outbreaks-table',
  reactsTo: ['geography'],
  async render(ctx: WidgetCtx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No active outbreaks for the selected filters.'); return; }
    const t = ctx.theme;

    const shaped = rows.map(r => ({
      id: r.id ?? '-',
      disease: r.disease ?? '-',
      region: regionName(String(r.region ?? '')),
      woreda: r.woreda ?? '-',
      declared: r.declared ?? '-',
      days: Number(r.days ?? 0),
      total: Number(r.total ?? 0).toLocaleString(),
      new_7d: Number(r.new_7d ?? 0),
      cfr: `${Number(r.cfr_pct ?? 0).toFixed(1)}%`,
      status: String(r.status ?? '-'),
      rrt: r.rrt ?? '-',
      readiness: String(r.readiness ?? '-'),
    }));
    const columns: TableColumn[] = [
      { id: 'id', label: 'ID' },
      { id: 'disease', label: 'Disease' },
      { id: 'region', label: 'Region' },
      { id: 'woreda', label: 'Woreda' },
      { id: 'declared', label: 'Declared' },
      { id: 'days', label: 'Days' },
      { id: 'total', label: 'Total' },
      { id: 'new_7d', label: 'New 7D' },
      { id: 'cfr', label: 'CFR' },
      { id: 'status', label: 'Status', format: 'pill', tone: STATUS_TONE },
      { id: 'rrt', label: 'RRT' },
      { id: 'readiness', label: 'Readiness', format: 'pill', tone: READY_TONE },
    ];

    ctx.el.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:flex-end;gap:1rem;margin-bottom:.5rem">
        <div style="display:flex;align-items:center;gap:.7rem;flex-shrink:0">
          <button type="button" class="dz-ob-sub" style="border:1px solid ${t.line};background:${t.paper};color:${t.ink[1]};border-radius:8px;padding:.3rem .7rem;font-size:.78rem;font-weight:600;cursor:pointer">Subscribe to alerts</button>
          <span style="font-size:.72rem;color:${t.ink[3]}">${rows.length.toLocaleString()} active outbreaks</span>
        </div>
      </div>
      <div class="dz-ob-tbl"></div>`;
    const sub = ctx.el.querySelector<HTMLButtonElement>('.dz-ob-sub');
    if (sub) sub.onclick = () => ctx.host.openSubscribe?.('all', ctx.filters.geography);
    const mount = ctx.el.querySelector<HTMLElement>('.dz-ob-tbl')!;
    return paginatedTable(mount, shaped, columns, ctx.theme, { search: true, sort: true });
  },
};
export default def;
