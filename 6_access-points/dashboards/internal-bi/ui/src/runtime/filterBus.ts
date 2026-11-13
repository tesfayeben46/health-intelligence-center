/**
 * Filter bus: the single owner of global-filter state and the only channel for
 * cross-widget interaction. Widgets publish on named dimensions (a map click
 * sets `geography`) and subscribe through their `reactsTo` list; there are no
 * free-form widget-to-widget messages, so every interaction is auditable.
 *
 * The bus is backed by the biApp filter state used by the GlobalFilter chrome:
 * biApp.setFilter is the writer of record; the bus mirrors reads and forwards
 * writes to it.
 */
import type { FilterState, FilterBus } from './types';
import { getBiApp } from '../biApp';

type Listener = { dims: string[]; cb: (s: FilterState) => void };
const listeners = new Set<Listener>();

function snapshot(): FilterState {
  const f = getBiApp()?.filters || {};
  return { geography: f.geography ?? 'all', programme: f.programme ?? 'all', period: f.period ?? 'all', ...f };
}

/** Fire subscribers whose declared dims intersect `changed` (or all, if the
 *  change set is unknown). Called by the shell after the GlobalFilter mutates. */
export function emitFilterChange(changed?: string[]): void {
  const s = snapshot();
  for (const l of listeners) {
    if (!changed || l.dims.length === 0 || l.dims.some(d => changed.includes(d))) l.cb(s);
  }
}

export const filterBus: FilterBus = {
  get: snapshot,
  set(dim, value) {
    const app = getBiApp();
    if (app?.setFilter) app.setFilter(dim, value);   // biApp stores the value and re-renders
    emitFilterChange([dim]);
  },
  subscribe(dims, cb) {
    const l: Listener = { dims, cb };
    listeners.add(l);
    return () => listeners.delete(l);
  },
};
