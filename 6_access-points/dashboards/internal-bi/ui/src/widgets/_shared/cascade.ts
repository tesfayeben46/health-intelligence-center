/**
 * Shared programme-cascade renderer. Each sv-cascade-<prog> widget has its own catalog id and
 * data_url (the programme is fixed in the fetcher's SQL), so the render logic is identical:
 * fetch the cascade steps, order them, draw a funnel. Renders an empty state when there is no data.
 * Data contract: {stage:str, pct:num} per step (stage 1 = 100%).
 */
import type { WidgetCtx } from '../../runtime/types';
import { funnelCascade } from './charts';

export async function renderCascade(ctx: WidgetCtx): Promise<void | (() => void)> {
  const rows = await ctx.fetchData();
  if (!rows || !rows.length) { ctx.empty('No cascade data for the selected filters.'); return; }
  const data = rows
    .slice()
    .sort((a, b) => (a.step_order ?? 0) - (b.step_order ?? 0))
    .map(r => ({
      name: String(r.stage ?? r.step_indicator ?? r.name ?? '-'),
      value: Number(r.pct ?? r.retention_pct_vs_entry ?? r.value ?? 0) || 0,
    }))
    .filter(d => d.value > 0);
  if (!data.length) { ctx.empty('No cascade data for the selected filters.'); return; }
  return funnelCascade(ctx.el, data.map(d => ({ name: d.name, pct: d.value })), ctx.theme);
}
