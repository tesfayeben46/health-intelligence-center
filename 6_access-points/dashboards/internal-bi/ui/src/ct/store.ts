/** Shared Control-Tower state, so the migrated CT widgets (filters, KPIs, movement
 *  network, dispatch) interact across separate widget mounts exactly as the
 *  standalone app did through App.tsx's useState: one store, many subscribers. */
import { useSyncExternalStore } from 'react';
import { allSlicer, type Slicer } from './lib/select';
import { SELECTED_ID } from './data/runtime';

export type CTView = 'routes' | 'network' | 'flow';
export interface CTState { slicer: Slicer; selectedId: string; view: CTView; focusNonce: number; dataNonce: number; }

let state: CTState = { slicer: allSlicer(), selectedId: SELECTED_ID, view: 'routes', focusNonce: 0, dataNonce: 0 };
const subs = new Set<() => void>();

export const getCT = (): CTState => state;
export const setCT = (patch: Partial<CTState>): void => { state = { ...state, ...patch }; subs.forEach(f => f()); };
export const setSlice = (patch: Partial<Slicer>): void => setCT({ slicer: { ...state.slicer, ...patch } });
export const resetSlice = (): void => setCT({ slicer: allSlicer() });
export const bumpFocus = (): void => setCT({ focusNonce: state.focusNonce + 1 });
/** Re-render after a local data change (a recorded decision) without re-scoping. */
export const bumpView = (): void => setCT({ dataNonce: state.dataNonce + 1 });
/** Real data landed: re-scope the view to the plan's own period and selection
 *  (the pre-load state has neither), keeping any place/product the user already chose. */
export const bumpData = (): void => setCT({
  dataNonce: state.dataNonce + 1,
  slicer: { ...state.slicer, period: allSlicer().period },
  selectedId: SELECTED_ID,
});

const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };
export const useCT = (): CTState => useSyncExternalStore(subscribe, getCT, getCT);
