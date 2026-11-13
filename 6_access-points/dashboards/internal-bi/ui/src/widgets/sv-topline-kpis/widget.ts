/**
 * Service top-line KPIs (top of the Service page).
 * Verification Ratio (matched EMR events / DHIS2 reported, EMR-active),
 * Stockout-Attributable Service Loss, Effective Coverage (quality-adjusted), True Cohort Retention.
 * EMR-derived reads cover EMR-active facilities, not the nation; the subtitles say so.
 * Source: EMR + DHIS2 + Supply + MFR.
 *
 * Data contract, one row per tile:
 *   metric · value:num · unit:'percent'|'count' · subtitle · delta_text · target_text · icon
 * reactsTo: geography · programme · period.
 */
import type { WidgetDef } from '../../runtime/types';
import { kpiStrip, type KpiCell } from '../_shared/charts';

const def: WidgetDef = {
  id: 'sv-topline-kpis',
  frameless: true,   // the KPI tiles are the cards: no wrapping card
  reactsTo: ['geography', 'programme', 'period'],
  async render(ctx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No service headline metrics for the selected filters.'); return; }
    const t = ctx.theme;
    // Colored icon chips (matching the Overview Headline cards). Normalise the row's icon names
    // to valid sprite ids; assign a distinct on-palette accent per tile.
    const ICON: Record<string, string> = { 'shield-check': 'i-shield-check', 'package-x': 'i-package', 'target': 'i-check-circle', 'users': 'i-users', 'activity': 'i-check-circle' };
    const accents = [t.status.info, t.status.alert, t.status.watch, t.status.good];
    const cells: KpiCell[] = rows.map((r, i) => {
      const unit = String(r.unit ?? 'count');
      const val = unit === 'percent' ? `${Number(r.value ?? 0)}%` : Number(r.value ?? 0).toLocaleString();
      const dt = String(r.delta_text ?? '');
      const raw = String(r.icon ?? '');
      return {
        label: String(r.metric ?? ''),
        value: val,
        sub: r.subtitle ? String(r.subtitle) : undefined,
        delta: dt || undefined,
        deltaDir: dt.startsWith('-') || dt.startsWith('▼') ? 'down' : 'up',
        foot: r.target_text ? String(r.target_text) : undefined,
        icon: ICON[raw] || (raw ? `i-${raw}` : undefined),
        iconColor: accents[i % accents.length],
      };
    });
    return kpiStrip(ctx.el, cells, t, 4);
  },
};
export default def;
