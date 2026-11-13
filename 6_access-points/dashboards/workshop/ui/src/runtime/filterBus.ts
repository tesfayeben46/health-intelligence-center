/**
 * Filter bus: the single owner of cross-widget selection state and the only
 * channel for cross-widget interaction. Widgets publish on named dimensions
 * (the models table sets `ws_model`, the projects table sets `ws_project`)
 * and subscribe via their `reactsTo` list; there are no free-form
 * widget-to-widget messages (one state owner, auditable interactions).
 *
 * Unlike internal-bi, whose bus bridges to the biApp filter store, this bus
 * owns the state. The global dims keep their defaults for parity with the
 * runtime types; the workshop's own dims are the master-detail selections
 * (`ws_model`, `ws_project`) plus refresh signals.
 */
import type { FilterState, FilterBus } from './types';

const state: FilterState = {
  geography: 'all',
  programme: 'all',
  period: 'last_12wk',
  ws_model: '',
  ws_project: '',
};

type Listener = { dims: string[]; cb: (s: FilterState) => void };
const listeners = new Set<Listener>();

function snapshot(): FilterState {
  return { ...state };
}

/** Fire subscribers whose declared dims intersect `changed` (or all, if the
 *  change set is unknown). */
export function emitFilterChange(changed?: string[]): void {
  const s = snapshot();
  for (const l of listeners) {
    if (!changed || l.dims.length === 0 || l.dims.some(d => changed.includes(d))) {
      try { l.cb(s); } catch { /* one bad subscriber never breaks the bus */ }
    }
  }
}

export const filterBus: FilterBus = {
  get: snapshot,
  set(dim, value) {
    if (state[dim] === value) return;   // idempotent: the same value triggers no redraw
    state[dim] = value;
    emitFilterChange([dim]);
  },
  subscribe(dims, cb) {
    const l: Listener = { dims, cb };
    listeners.add(l);
    return () => listeners.delete(l);
  },
};
