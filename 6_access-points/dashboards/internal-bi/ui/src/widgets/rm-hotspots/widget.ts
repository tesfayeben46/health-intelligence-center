/**
 * Supply Hotspots: worst regions weighted by service volume (Resource page).
 * Service volume makes a stockout at a high-throughput region rank above one at a quiet
 * region, turning a stock list into an impact list.
 * Source: bi_supply + DHIS2 (service volume) + MFR.
 * Data contract: rank · region · composite:num · exposed_services:int ·
 *   service_volume_month:int. reactsTo: geography.
 *
 * Rendered with the shared paginatedTable. Rows arrive ranked worst-first; the top-3
 * rows' composite score is drawn in alert red via TableColumn.cellClass (index < 3).
 * There is no trend column: no data source feeds one.
 */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import type { TableColumn } from '../_shared/charts';
import { paginatedTable } from '../_shared/charts';
import { regionName } from '../_shared/reference';

const kfmt = (n: number) => (n >= 1000 ? `${Math.round(n / 1000)}K` : String(n));

const def: WidgetDef = {
  id: 'rm-hotspots',
  reactsTo: ['geography'],
  async render(ctx: WidgetCtx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No supply hotspot rows for the selected filters.'); return; }
    const t = ctx.theme;

    const shaped = rows.map(r => ({
      rank: r.rank ?? '',
      region: regionName(String(r.region ?? '')),
      composite: Number(r.composite ?? 0).toFixed(1),
      exposed: Number(r.exposed_services ?? 0).toLocaleString(),
      volume: kfmt(Number(r.service_volume_month ?? 0)),
    }));
    const columns: TableColumn[] = [
      { id: 'rank', label: '#' },
      { id: 'region', label: 'Region' },
      // Top-3 hotspots (rows arrive ranked worst-first) get the composite in alert-red.
      { id: 'composite', label: 'Composite', cellClass: (_v, _row, i) => (i < 3 ? `color:${t.status.alert};font-weight:700` : '') },
      { id: 'exposed', label: 'Exposed services' },
      { id: 'volume', label: 'Service volume /mo' },
    ];

    // A one-line executive lead sits above the table (no nested card): paginatedTable
    // clears its own container, so the table mounts into a child while the lead stays.
    ctx.el.innerHTML = `<p class="widget-lead" style="margin:0 0 .6rem;font-size:.8rem;line-height:1.35;color:var(--ink-3)">Regions ranked worst first and weighted by service volume, so stockouts at busy facilities rise to the top.</p><div class="rmh-table" style="flex:1;min-height:0"></div>`;
    const mount = ctx.el.querySelector('.rmh-table') as HTMLElement;
    // No search/sort: the top-3 highlight relies on the incoming worst-first order.
    return paginatedTable(mount, shaped, columns, ctx.theme, {});
  },
};
export default def;
