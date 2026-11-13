/**
 * Act-First Woredas: overview action queue.
 * "Where disease signal meets weak readiness this week." Ranked worst-first by an
 * action score; priority band High/Immediate · Elevated/This week · Watch.
 *
 * Data contract (mart): grain one row per woreda signal in the active period:
 *   rank:int · woreda:str · region:str · p_alarm:int(%) · readiness_index:int ·
 *   reporting_reliability_pct:int · evidence:str · action_priority
 * reactsTo: geography · programme · period.
 *
 * Rendered with the shared paginatedTable (sticky header, search, sort,
 * pagination, horizontal scroll), the standard data table.
 */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import type { TableColumn } from '../_shared/charts';
import { paginatedTable } from '../_shared/charts';
import { regionName } from '../_shared/reference';

const PILL_TONE: Record<string, string> = {
  High: 'alert', Immediate: 'alert',
  Elevated: 'watch', 'This week': 'watch', Watch: 'watch',
};

const def: WidgetDef = {
  id: 'ov-act-first-woredas',
  reactsTo: ['geography', 'programme', 'period'],
  async render(ctx: WidgetCtx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No action-priority woredas for the selected filters.'); return; }
    const t = ctx.theme;

    const shaped = rows.map((r: any) => ({
      rank: Number(r.rank ?? 0),
      woreda: r.woreda ?? '-',
      region: regionName(String(r.region ?? '')),
      p_alarm: Number(r.p_alarm ?? 0),
      readiness: Number(r.readiness_index ?? 0),
      reliability: Number(r.reporting_reliability_pct ?? 0),
      evidence: r.evidence ?? '-',
      priority: String(r.action_priority ?? ''),
    }));

    const alertHex = t.status.alert;
    const columns: TableColumn[] = [
      { id: 'rank', label: '#',
        cellClass: (_v, row) => `font-weight:700;color:${(row.priority === 'High' || row.priority === 'Immediate') ? alertHex : t.status.watch}` },
      { id: 'woreda', label: 'Woreda', cellClass: () => `font-weight:600;color:${t.ink[0]}` },
      { id: 'region', label: 'Region' },
      { id: 'p_alarm', label: 'P(alarm)', format: 'pct',
        cellClass: (v) => Number(v) >= 60 ? `color:${alertHex};font-weight:700` : '' },
      { id: 'readiness', label: 'Readiness' },
      { id: 'reliability', label: 'Reporting %', format: 'pct' },
      { id: 'evidence', label: 'Evidence (this week)' },
      { id: 'priority', label: 'Action priority', format: 'pill', tone: PILL_TONE },
    ];

    ctx.el.innerHTML = `
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;margin-bottom:.6rem">
        <p style="font-size:.82rem;color:${t.ink[2]};margin:0">Where disease signal meets weak readiness this week</p>
        <button type="button" class="ov-af-link" style="flex-shrink:0;background:${t.paper};color:${t.ink[1]};
          border-radius:8px;padding:.3rem .7rem;font-size:.78rem;font-weight:600;cursor:pointer;box-shadow:0 1px 2px rgba(15,23,42,.08)">View full action queue</button>
      </div>
      <div class="ov-af-table"></div>`;

    const link = ctx.el.querySelector<HTMLButtonElement>('.ov-af-link');
    if (link) link.onclick = () => { ctx.host.navigateToPage('disease'); ctx.host.focusWidget('dz-woreda-table'); };

    return paginatedTable(ctx.el.querySelector<HTMLElement>('.ov-af-table')!, shaped, columns, ctx.theme,
                          { search: true, sort: true, pageSize: 12 });
  },
};
export default def;
