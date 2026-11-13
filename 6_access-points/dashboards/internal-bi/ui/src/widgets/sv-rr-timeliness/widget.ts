/**
 * Reporting timeliness (Service): share of facility requisitions that arrived
 * within the governed deadline, by month, with the measured lag.
 *
 * Profile-scoped: this is the one timeliness figure in the warehouse that is
 * measured rather than assumed, and it needs a per-report arrival timestamp at
 * the source. The eLMIS archive carries one (73,925 distinct submission times
 * across 73,926 requisitions). The six snapshot-loaded sources carry only a bulk
 * load stamp, and a percentage computed from that would measure the loader, not
 * the reporter, so profiles without a timing-bearing source disable this widget
 * in their catalog.yaml.
 *
 * Data contract (bi_supply.mart_elmis_hist_timeliness, month by programme):
 *   month_id:str · programme_name:str · requisitions:int ·
 *   on_time_requisitions:int · on_time_pct:num · median_lag_days:num
 * reactsTo: period.
 */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import type { TableColumn } from '../_shared/charts';
import { paginatedTable } from '../_shared/charts';

/** Band the on-time share the way the rest of the dashboard bands a percentage,
 *  so this column reads like every other percentage on the page. */
const tone = (pct: number): string =>
  pct >= 90 ? 'good' : pct >= 75 ? 'watch' : 'alert';

const def: WidgetDef = {
  id: 'sv-rr-timeliness',
  reactsTo: ['period'],
  async render(ctx: WidgetCtx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) {
      ctx.empty('No reporting-timeliness data: this view needs a source that '
              + 'records when each report arrived.');
      return;
    }

    // Fold programmes together: the question here is national reporting
    // discipline by month. A per-programme split is a different view and would
    // belong in its own widget rather than a wider table.
    const byMonth = new Map<string, { onTime: number; total: number; lags: number[] }>();
    for (const r of rows) {
      const m = String(r.month_id ?? '');
      if (!m) continue;
      const acc = byMonth.get(m) ?? { onTime: 0, total: 0, lags: [] };
      acc.onTime += Number(r.on_time_requisitions ?? 0);
      acc.total  += Number(r.requisitions ?? 0);
      const lag = Number(r.median_lag_days);
      if (Number.isFinite(lag)) acc.lags.push(lag);
      byMonth.set(m, acc);
    }

    const shaped = [...byMonth.entries()]
      .sort((a, b) => (a[0] < b[0] ? 1 : -1))          // newest month first
      .map(([month, a]) => {
        const pct = a.total ? +((100 * a.onTime) / a.total).toFixed(1) : 0;
        // Median OF the per-programme medians: not a pooled median, because the
        // mart does not expose the raw lags. The column label says so rather
        // than passing one statistic off as the other.
        const s = [...a.lags].sort((x, y) => x - y);
        const mid = s.length >> 1;
        const lag = !s.length ? 0
          : +(s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2).toFixed(1);
        return {
          month,
          on_time: pct,
          band: tone(pct),
          submitted: a.total.toLocaleString(),
          late: (a.total - a.onTime).toLocaleString(),
          lag,
        };
      });

    const columns: TableColumn[] = [
      { id: 'month', label: 'Month' },
      { id: 'on_time', label: 'On time', format: 'pct',
        cellClass: v => (Number(v) < 75 ? 'color:#EF4444;font-weight:700' : '') },
      { id: 'band', label: 'Band', format: 'pill' },
      { id: 'submitted', label: 'Requisitions' },
      { id: 'late', label: 'Late' },
      { id: 'lag', label: 'Median lag (days)' },
    ];

    return paginatedTable(ctx.el, shaped, columns, ctx.theme,
                          { pageSize: 12, search: false, sort: true });
  },
};

export default def;
