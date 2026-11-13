/** Source pulse: per-source trust tiles from bi_quality.mart_data_quality.
 *  Only the warehouse sees every feed side by side. Completeness is graded
 *  against the reporting norms (>=90 ok / 80 to 90 watch / <80 poor); sources
 *  without a computed metric show a "no metric" state. */
import type { WidgetDef } from '../../runtime/types';

const CHIP: Record<string, string> = {
  ok: 'var(--good)', watch: 'var(--watch)', poor: 'var(--alert)',
  no_metric: 'var(--ink-4, #9ca3af)',
};

const def: WidgetDef = {
  id: 'ov-source-pulse',
  reactsTo: ['geography'],
  async render(ctx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No source-quality rows in the current scope.'); return; }
    const wrap = document.createElement('div');
    wrap.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;padding:2px;';
    for (const r of rows) {
      const status = String(r.status ?? 'no_metric');
      const color = CHIP[status] ?? CHIP.no_metric;
      const compl = r.completeness_pct == null ? 'n/a' : `${Number(r.completeness_pct).toFixed(1)}%`;
      const reports = r.reports_received == null ? '' :
        `${Number(r.reports_received).toLocaleString()} / ${Number(r.reports_expected ?? 0).toLocaleString()} reports`;
      const tile = document.createElement('div');
      // Borderless soft card (cards never have borders; the status colour
      // moves from a left-accent border to a small dot).
      tile.style.cssText =
        'border-radius:12px;box-shadow:0 1px 2px rgba(15,23,42,.05),0 5px 16px rgba(15,23,42,.045);' +
        'padding:12px 14px;display:flex;flex-direction:column;gap:3px;background:var(--paper);';
      tile.innerHTML =
        `<span style="font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3,#667085);display:flex;align-items:center;gap:6px"><span style="width:7px;height:7px;border-radius:50%;background:${color};flex:none"></span>${String(r.source)}</span>` +
        `<span style="font-size:22px;font-weight:650;color:${status === 'no_metric' ? 'var(--ink-3,#667085)' : 'var(--ink)'};">${compl}</span>` +
        `<span style="font-size:11px;color:var(--ink-3,#667085);">${reports}</span>` +
        `<span style="font-size:11px;color:var(--ink-4,#9ca3af);">latest ${String(r.latest_period ?? 'n/a')}, ` +
        `<span style="color:${color};font-weight:600;">${status.replace('_', ' ')}</span></span>`;
      wrap.appendChild(tile);
    }
    ctx.el.appendChild(wrap);
  },
};
export default def;
