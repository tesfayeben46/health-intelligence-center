/**
 * rm-stockout-risk-kpi: Commodities at stockout risk (ML-model serving tile).
 *
 * The model-to-BI serving seam of the ML lifecycle: train to predict-batch
 * (ops_ml.ml_predictions) to this KPI. It reads the `stockout_forecast` model's
 * latest batch predictions STRAIGHT from the governed gateway ML endpoint
 * (GET /api/ml/stockout_forecast/predictions, class PRG/DC.PRO: analysts &
 * decision-makers), via fetchEntity. No mart and no gateway fetcher: the ML
 * route is already OPA-gated, so binding to the model is just the path segment.
 *
 * Each prediction row is one commodity (the flat forecast schema stores
 * district = commodity_name) carrying attrs.projected_days_remaining from the
 * Holt-Winters consumption forecast. The headline counts commodities whose
 * projected cover falls inside the replenishment HORIZON. Everything is
 * A denied or empty read renders the standard empty state.
 */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { kpiStrip, type KpiCell } from '../_shared/charts';

const MODEL = 'stockout_forecast';
// One procurement/replenishment cycle. Stated in the tile so the count is never
// ambiguous; the model itself emits projected days-of-cover per commodity.
const HORIZON_DAYS = 90;

/** projected days-of-cover for a prediction row (jsonb attrs to number). */
function daysLeft(r: Record<string, any>): number {
  const v = Number(r?.attrs?.projected_days_remaining);
  return Number.isFinite(v) ? v : Infinity;
}

const def: WidgetDef = {
  id: 'rm-stockout-risk-kpi',
  // The ML endpoint returns the latest batch run for the whole commodity set;
  // it is not geography/period sliced, so this tile does not react to the bar.
  reactsTo: [],
  async render(ctx: WidgetCtx) {
    const rows = await ctx.fetchEntity(`/gw/ml/${MODEL}/predictions`, { limit: '500' });
    if (!rows || rows.length === 0) {
      ctx.empty('No stockout forecast available yet: run the stockout_forecast '
        + 'batch prediction to populate this.');
      return;
    }

    const total = rows.length;
    const atRisk = rows.filter(r => daysLeft(r) < HORIZON_DAYS).length;
    const soonest = rows.reduce((a, b) => (daysLeft(b) < daysLeft(a) ? b : a));
    const soonestDays = daysLeft(soonest);
    const t = ctx.theme;

    const cells: KpiCell[] = [
      {
        icon: 'i-alert',
        iconColor: atRisk > 0 ? t.status.alert : t.status.good,
        label: 'Commodities at stockout risk',
        value: String(atRisk),
        status: atRisk > 0 ? 'alert' : 'good',
        foot: `within ${HORIZON_DAYS} days · ${MODEL} @champion`,
      },
      {
        icon: 'i-package',
        iconColor: t.status.watch,
        label: 'Soonest to run out',
        value: (soonest.district as string) || 'n/a',
        sub: Number.isFinite(soonestDays)
          ? `${Math.round(soonestDays)} days of cover left`
          : 'cover unknown',
        status: 'watch',
      },
      {
        icon: 'i-info',
        iconColor: t.status.info,
        label: 'Commodities forecast',
        value: String(total),
        status: 'info',
        foot: 'latest batch run',
      },
    ];
    return kpiStrip(ctx.el, cells, t, 3);
  },
};
export default def;
