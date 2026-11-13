/**
 * National Service Reliability: ranked-region reliability headline on Overview.
 *
 * Fuses coverage, coverage-confidence and commodity-readiness into a single composite
 * score (0 to 100) per region, worst-first. Composite bands
 * (Low <60 · Moderate 60 to 70 · Strong >70) map to status tokens alert/watch/good.
 *
 * Data contract:
 *   grain: one row per region by current period by programme scope. columns:
 *     rank:int · region:str · composite_score:int(0 to 100) · coverage_pct:int ·
 *     coverage_confidence_pct:int · commodity_readiness_pct:int · pop_weight_status:str
 *   composite_score = 0.40*coverage + 0.35*coverage_confidence + 0.25*commodity_readiness
 *   The widget renders the supplied score; the mart computes it. Population
 *   weighting shows as "pending" while pop_weight_status says so.
 * reactsTo: geography · programme · period (Overview global filters).
 *
 * Rendered with the shared paginatedTable (sticky header, fixed-height scroll).
 * Rows arrive ranked worst-first; the "#" column carries that display rank.
 * The rank + composite-score cells are coloured by band via TableColumn.cellClass
 * (Low <60: alert · Moderate 60-70: watch · Strong >70: good). The "Pop-weighting pending"
 * pill and the band legend footer live outside the table mount (they survive redraws).
 */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import type { TableColumn } from '../_shared/charts';
import { paginatedTable } from '../_shared/charts';
import { regionName } from '../_shared/reference';

/** Composite band: status colour (<60 low, 60 to 70 moderate, >70 strong). */
function bandColor(v: number, theme: WidgetCtx['theme']): string {
  if (v < 60) return theme.status.alert;
  if (v <= 70) return theme.status.watch;
  return theme.status.good;
}

const def: WidgetDef = {
  id: 'ov-reliability-table',
  reactsTo: ['geography'],
  async render(ctx: WidgetCtx) {
    const fetched = await ctx.fetchData();
    if (!fetched || !fetched.length) { ctx.empty('No regional reliability data for the current scope.'); return; }
    const t = ctx.theme;

    // Display ranking is worst-first (the design reads "act on rank 1 first");
    // rank chip = position in this display ordering, derived from the real scores.
    const shaped = [...fetched]
      .sort((a, b) => Number(a.composite_score ?? 0) - Number(b.composite_score ?? 0))
      .map((r, i) => ({
        rank: i + 1,
        region: regionName(String(r.region ?? '')),
        coverage: Number(r.coverage_pct ?? 0),
        coverage_confidence: Number(r.coverage_confidence_pct ?? 0),
        commodity_readiness: Number(r.commodity_readiness_pct ?? 0),
        composite: Number(r.composite_score ?? 0),
      }));
    // Both rank and composite are banded on the same composite score (row.composite).
    const bandStyle = (_v: any, row: Record<string, any>) => `color:${bandColor(Number(row.composite), t)};font-weight:700`;
    const columns: TableColumn[] = [
      { id: 'rank', label: '#', cellClass: bandStyle },
      { id: 'region', label: 'Region' },
      { id: 'coverage', label: 'Coverage', format: 'pct' },
      { id: 'coverage_confidence', label: 'Coverage confidence', format: 'pct' },
      { id: 'commodity_readiness', label: 'Commodity readiness', format: 'pct' },
      { id: 'composite', label: 'Composite score', cellClass: bandStyle },
    ];

    const dot = (col: string, label: string) =>
      `<span style="display:inline-flex;align-items:center;gap:.35rem;color:${t.ink[2]};font-size:.74rem">
         <span style="width:7px;height:7px;border-radius:50%;background:${col}"></span>${label}</span>`;

    ctx.el.innerHTML = `
      <div style="display:flex;justify-content:flex-end;margin-bottom:.6rem">
        <span style="font-size:.72rem;color:${t.ink[3]};border:1px solid ${t.line};border-radius:999px;padding:.15rem .6rem">Pop-weighting pending</span>
      </div>
      <div class="ov-rt-tbl"></div>
      <div style="display:flex;align-items:center;gap:1rem;margin-top:.7rem;padding-top:.6rem;border-top:1px solid ${t.line}">
        ${dot(t.status.alert, 'Low (&lt;60)')}
        ${dot(t.status.watch, 'Moderate (60 to 70)')}
        ${dot(t.status.good, 'Strong (&gt;70)')}
      </div>`;
    const mount = ctx.el.querySelector<HTMLElement>('.ov-rt-tbl')!;
    return paginatedTable(mount, shaped, columns, ctx.theme, { search: true, sort: true, pageSize: 12 });
  },
};
export default def;
