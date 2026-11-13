/**
 * Global-filter reactivity: the single definition of scope visibility and
 * reflow arithmetic. Used by:
 *
 *   · PageRenderer: hides/shows widget cards and reflows the section grid on
 *     every filter-bus change;
 *   · widgets/_shared/localFilters.ts: pins widget-local dimension controls to
 *     an active global value, using the same definition of "active", so a
 *     widget never contradicts the scope that hides its neighbours.
 *
 * Gateway/UI contract:
 *   · Bound widgets in the resolved spec carry `supports: {geography,
 *     programme, period}` (api/spec.ts WidgetSupports).
 *   · A widget is hidden iff some global dimension is active (value ≠ 'all')
 *     and the widget declares supports[dim] === false.
 *   · A widget without a supports key is never hidden.
 *   · Period follows the same rule: supports.period === false hides the widget
 *     while a period is active. ('all' is reachable for period through the
 *     global filter's Reset.)
 */
import type { FilterState } from './types';
import type { ResolvedWidget } from '../api/spec';

/** The named global dimensions the reactivity contract covers. `commodity` is a
 *  first-class dim like `programme` (a plain UI narrowing filter offered on the
 *  Resource page): a widget declaring supports.commodity === false hides when a
 *  commodity is picked; one that supports it re-fetches narrowed to it. It is
 *  never active on pages that don't offer the commodity filter, so it is inert
 *  everywhere else. */
export const GLOBAL_DIMS = ['geography', 'programme', 'period', 'commodity'] as const;

/** A dimension is active when it holds any value other than 'all'. */
export function isDimActive(state: FilterState, dim: string): boolean {
  const v = state[dim];
  return v != null && v !== '' && v !== 'all';
}

/** The subset of GLOBAL_DIMS currently active. */
export function activeDims(state: FilterState): string[] {
  return GLOBAL_DIMS.filter(d => isDimActive(state, d));
}

/** Scope visibility: hidden iff ∃ an active dim the widget declares it cannot
 *  honour (supports[dim] === false). No supports key: never hidden. */
export function isWidgetHiddenAtScope(w: ResolvedWidget, state: FilterState): boolean {
  const sup = w.supports;
  if (!sup || typeof sup !== 'object') return false;   // safety fallback
  return GLOBAL_DIMS.some(d => sup[d] === false && isDimActive(state, d));
}

/** Dimension to the query param the gateway narrows on. geography/programme/period are
 *  accepted directly (the gateway aliases region/geography server-side). */
const DIM_PARAM: Record<string, string> = {
  geography: 'geography', programme: 'programme', period: 'period',
  model: 'model', commodity: 'commodity',
};

/**
 * The narrowing params to inject into a widget's data fetch. For each dimension
 * the widget reacts to, send the active value iff the widget's fetcher supports
 * that dimension (supports[dim] !== false). A national or opted-out widget
 * (reactsTo:[] or supports[dim] === false) sends nothing for that dimension.
 * The server still caps every value against role_scope ∩ user_scope (gateway
 * permissions.py), so injection can only narrow a role's scope, never widen it.
 * Widget-supplied params (local selectors) win over these.
 */
export function filterParamsForWidget(
  w: ResolvedWidget, reacts: readonly string[], state: FilterState,
): Record<string, string> {
  const out: Record<string, string> = {};
  const sup = w.supports;
  for (const dim of reacts) {
    if (!isDimActive(state, dim)) continue;
    if (sup && sup[dim] === false) continue;   // fetcher can't narrow this dim: skip
    out[DIM_PARAM[dim] || dim] = String(state[dim]);
  }
  return out;
}

/**
 * The dimensions a widget re-fetches and re-renders on when a filter changes.
 * An explicit reactsTo (WidgetDef.reactsTo, else the catalog reacts_to) wins;
 * otherwise it defaults to the dimensions the widget's fetcher supports, so a
 * widget that declares no reactsTo still follows the filters it can honour.
 * Unbound widgets (supports == null) react to nothing.
 */
export function effectiveReactsTo(
  reactsTo: readonly string[] | undefined,
  catalogReacts: readonly string[] | undefined,
  supports: Record<string, boolean> | null | undefined,
): string[] {
  if (reactsTo && reactsTo.length) return [...reactsTo];
  if (catalogReacts && catalogReacts.length) return [...catalogReacts];
  return supports ? GLOBAL_DIMS.filter(d => supports[d] === true) : [];
}

/**
 * Grid redistribution: pack widgets greedily into rows by their declared
 * col_span (a widget that would overflow the row starts the next one), then
 * grow each underfilled row's widgets in proportion to their declared spans
 * so the row fills all `cols` tracks (largest-remainder rounding keeps the
 * sum exact). Rows that already sum to `cols` come back unchanged, so the
 * at-rest layout is identical to the catalog's declared spans.
 *
 * Returns the effective span per input index. Pure; used for the initial
 * paint and every scope reflow, so there is one layout algorithm.
 */
export function packRows(declared: number[], cols: number): number[] {
  const out = declared.slice();
  let row: number[] = [];
  let sum = 0;
  const flush = () => {
    if (row.length && sum < cols) {
      const exact = row.map(i => (declared[i] * cols) / sum);
      const base = exact.map(Math.floor);
      let used = base.reduce((a, b) => a + b, 0);
      // Hand the remaining tracks to the largest fractional remainders.
      const order = row.map((_, k) => k)
        .sort((a, b) => (exact[b] - base[b]) - (exact[a] - base[a]));
      for (let k = 0; used < cols && order.length; k++, used++) base[order[k % order.length]]++;
      row.forEach((i, k) => { out[i] = base[k]; });
    }
    row = []; sum = 0;
  };
  declared.forEach((s, i) => {
    if (sum + s > cols && row.length) flush();
    row.push(i); sum += s;
  });
  flush();
  return out;
}
