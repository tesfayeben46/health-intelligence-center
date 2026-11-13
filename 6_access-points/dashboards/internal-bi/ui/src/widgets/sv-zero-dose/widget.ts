/** Zero-dose children by vaccine last mile, from three feeds (DHIS2 immunization + population, mBrana cold-chain).
 *  expected_infants is a labelled estimate (population x 3.2% CBR). Hotspot =
 *  many unreached children and a struggling supply chain to reach them. */
import type { WidgetDef } from '../../runtime/types';
import { paginatedTable } from '../_shared/charts';
import { regionName } from '../_shared/reference';

const def: WidgetDef = {
  id: 'sv-zero-dose',
  reactsTo: ['geography'],
  async render(ctx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No population-year data in the current scope.'); return; }
    const shaped = rows.map(r => ({
      region: regionName(r.region_id),
      infants: Number(r.expected_infants ?? 0).toLocaleString(),
      penta1: Number(r.penta1_annualized ?? 0).toLocaleString(),
      reach: r.reach_index == null ? 'n/a' : Number(r.reach_index).toFixed(1),
      cold_chain: r.mbrana_zero_stock_pct == null ? 'n/a' : `${Number(r.mbrana_zero_stock_pct).toFixed(1)}% logs at zero`,
      status: r.hotspot ? 'hotspot' : 'watch',
    }));
    const table = paginatedTable(ctx.el, shaped, [
      { id: 'region', label: 'Region' },
      { id: 'infants', label: 'Expected infants (est.)' },
      { id: 'penta1', label: 'Penta-1 (annualized)' },
      { id: 'reach', label: 'Reach / 1,000 infants' },
      { id: 'cold_chain', label: 'mBrana stock stress' },
      { id: 'status', label: 'Status', format: 'pill' },
    ], ctx.theme, { search: false, sort: true, pageSize: 14 });
    const note = document.createElement('div');
    note.style.cssText = 'font-size:11px;color:var(--ink-4,#9ca3af);padding:6px 2px 0;';
    note.textContent = 'Comparative reach index. Expected infants are estimated as 3.2% of population (crude birth rate); lowest-reach regions locate the unreached (zero-dose) children.';
    ctx.el.appendChild(note);
    return table;
  },
};
export default def;
