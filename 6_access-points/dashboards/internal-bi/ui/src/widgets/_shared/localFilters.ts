/**
 * Local-filter pinning: the required path for any widget-local control that
 * shares a dimension with the global filter.
 *
 *   When a global dim is active (value ≠ 'all'), a widget-local control on the
 *   same dim pins to the global value: it renders as a locked chip (the same
 *   markup as the global-filter widget's role-locked state), takes no user
 *   input, and the widget fetches and filters with the global value. When the
 *   global returns to 'all', the local control unlocks.
 *
 * The "active" definition is shared with the scope-visibility logic
 * (runtime/reactivity.ts) so hiding and pinning cannot disagree.
 *
 * No widget currently renders a local region/programme/period control; local
 * controls select models, metrics, formats or preferences on non-global
 * dimensions. Any widget that adds a geography/programme/period control must
 * route it through pinnedDims()/effectiveDimValue() and render lockedChipHtml()
 * while pinned. The st-display "default period" select is not pinned: it edits
 * a saved preference for future sessions, not the live filter state.
 */
import type { FilterBus } from '../../runtime/types';
import { GLOBAL_DIMS, isDimActive } from '../../runtime/reactivity';

/** Pin state for one global dimension. */
export interface DimPin {
  /** True when the global dim is active; the local control must lock to it. */
  pinned: boolean;
  /** The global value while pinned; 'all' (or the bus value) otherwise. */
  value: string;
}

/** Per-dim pin state from the live bus: {geography, programme, period}. */
export function pinnedDims(bus: FilterBus): Record<string, DimPin> {
  const s = bus.get();
  const out: Record<string, DimPin> = {};
  for (const d of GLOBAL_DIMS) out[d] = { pinned: isDimActive(s, d), value: s[d] ?? 'all' };
  return out;
}

/** The value a widget must fetch/filter with for `dim`: the global value while
 *  the dim is pinned (the user may not contradict the scope), else the
 *  widget's own local choice. */
export function effectiveDimValue(bus: FilterBus, dim: string, localChoice: string): string {
  const s = bus.get();
  return isDimActive(s, dim) ? s[dim] : localChoice;
}

const esc = (v: string) => String(v).replace(/[&<>"]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));

/** Locked-chip markup with the same classes and icon as the global-filter
 *  widget's role-locked chip, so a pinned local control reads as "this
 *  dimension is decided elsewhere". Render it in place of the widget's
 *  interactive control while the dim is pinned. */
export function lockedChipHtml(label: string, valueLabel: string): string {
  return `<span class="dropdown">
    <span class="filter-chip locked" title="Pinned to the global ${esc(label)} scope">
      <span class="key">${esc(label)}</span>
      <span class="val">${esc(valueLabel)}</span>
      <svg class="ico ico-sm"><use href="#i-lock"/></svg>
    </span></span>`;
}

/**
 * One-call helper for a widget's local dim control:
 *   returns null while the dim is not pinned (draw the interactive control),
 *   or the locked-chip HTML to draw instead (and fetch with pin.value).
 *
 *   const pin = pinnedDims(ctx.bus).geography;
 *   const chip = pinnedControlHtml(ctx.bus, 'geography', 'Region', labelFor);
 *   controls.innerHTML = chip ?? renderMyRegionSelect();
 */
export function pinnedControlHtml(
  bus: FilterBus,
  dim: string,
  label: string,
  labelFor?: (value: string) => string,
): string | null {
  const s = bus.get();
  if (!isDimActive(s, dim)) return null;
  const v = s[dim];
  return lockedChipHtml(label, labelFor ? labelFor(v) : v);
}
