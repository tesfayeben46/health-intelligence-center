/**
 * Highest-Risk Woredas by Composite Score (Service page, EMR-active).
 * risk_score = 0.40*coverage_gap + 0.35*normalized_stockout_days
 * + 0.25*(100 - reporting_completeness), normalised 0 to 100; High>=70 · Elevated 55 to 69 · Watch <55.
 * The EMR-active scope is stated and the formula shown, so the score can be checked.
 * Data contract: woreda · region · coverage_gap_pct · stockout_days ·
 *   reporting_completeness_pct · risk_score:int · risk_band. reactsTo: geography · programme · period.
 *
 * Rendered with the shared paginatedTable (sticky header, fixed-height scroll,
 * search + click-to-sort); rows arrive ranked worst-first. The Risk score (number) and
 * its Band (a tone-coloured pill: High to alert, Elevated to watch, Watch to neutral) are
 * separate columns.
 */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import type { TableColumn } from '../_shared/charts';
import { paginatedTable } from '../_shared/charts';
import { regionName, levelName } from '../_shared/reference';

const BAND_TONE: Record<string, string> = { High: 'alert', Elevated: 'watch', Watch: 'neutral' };

const def: WidgetDef = {
  id: 'sv-risk-woredas',
  reactsTo: ['geography', 'programme', 'period'],
  async render(ctx: WidgetCtx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty(`No high-risk ${levelName('admin3').toLowerCase()} rows for the selected filters.`); return; }
    const t = ctx.theme;

    const shaped = rows.map(r => ({
      woreda: r.woreda ?? '-',
      region: regionName(String(r.region ?? '')),
      coverage_gap: Number(r.coverage_gap_pct ?? 0),
      stockout_days: Number(r.stockout_days ?? 0),
      reporting_completeness: Number(r.reporting_completeness_pct ?? 0),
      risk_score: Number(r.risk_score ?? 0),
      band: String(r.risk_band ?? '-'),
    }));
    const columns: TableColumn[] = [
      { id: 'woreda', label: levelName('admin3') },
      { id: 'region', label: levelName('admin1') },
      { id: 'coverage_gap', label: 'Coverage gap', format: 'pct' },
      { id: 'stockout_days', label: 'Stockout days' },
      { id: 'reporting_completeness', label: 'Reporting completeness', format: 'pct' },
      { id: 'risk_score', label: 'Risk score' },
      { id: 'band', label: 'Band', format: 'pill', tone: BAND_TONE },
    ];

    ctx.el.innerHTML = `
      <p style="font-size:.82rem;color:${t.ink[2]};margin:0 0 .2rem">EMR-active, ranked worst-first</p>
      <div class="sv-rw-tbl"></div>`;
    const mount = ctx.el.querySelector<HTMLElement>('.sv-rw-tbl')!;
    return paginatedTable(mount, shaped, columns, ctx.theme, { search: true, sort: true });
  },
};
export default def;
