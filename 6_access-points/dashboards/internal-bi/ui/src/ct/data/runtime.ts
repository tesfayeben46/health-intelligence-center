/**
 * Runtime data holder for the Supply Control Tower.
 *
 * Empty until the mart data arrives via the gateway route `/gw/ct/plan` on first
 * mount. Every CT module imports the plan symbols from HERE instead of `plan.ts`
 * (which only supplies the types and `byId`), so ES live-bindings propagate the
 * data to all importers: `filterMovements`, the map, the rail and the dispatch table
 * all read the mart-backed data once it arrives.
 */
import * as STATIC from './plan';
import type { Movement } from './plan';
import { authFetch } from '../../api/useAuth';

// Re-export the (unchanging) type surface + helper so importers need nothing from plan.ts.
export type { Warehouse, DeliveryNode, Why, Movement, StopLine, VehicleStop, VehicleRun, Period } from './plan';
export { byId } from './plan';

// Mutable data bindings: empty until the mart-backed plan arrives from /gw/ct/plan.
// There is no bundled fallback plan: a fallback would show another
// country's data whenever the gateway blinks; the page shows its empty state instead.
export let REGIONS: typeof STATIC.REGIONS = [];
export let REGION_NAMES: typeof STATIC.REGION_NAMES = [];
export let PERIODS: typeof STATIC.PERIODS = [];
export let CURRENT_PERIOD: string = '';
export let COMMODITIES: typeof STATIC.COMMODITIES = [];
export let WAREHOUSES: typeof STATIC.WAREHOUSES = [];
export let NODES: typeof STATIC.NODES = [];
export let MOVEMENTS: typeof STATIC.MOVEMENTS = [];
export let VEHICLE_RUNS: typeof STATIC.VEHICLE_RUNS = [];
export let SELECTED_ID: string = '';
export let PERIOD_STATS: typeof STATIC.PERIOD_STATS = [];
export let SUMMARY: typeof STATIC.SUMMARY | null = null;
export let BASELINE: typeof STATIC.BASELINE | null = null;
export let WH_BY_ID: typeof STATIC.WH_BY_ID = {} as typeof STATIC.WH_BY_ID;
export let NODE_BY_ID: typeof STATIC.NODE_BY_ID = {} as typeof STATIC.NODE_BY_ID;
export let RUN_BY_ID: typeof STATIC.RUN_BY_ID = {} as typeof STATIC.RUN_BY_ID;
/** Stock-health KPI inputs per region at the latest reported month (numerator /
 *  denominator), pooled by the cards under the current Region filter. */
export interface KpiInput { regionId: string | null; kpi: string; category: string; month?: string; numerator: number; denominator: number; target: number | null; }
export let KPIS: KpiInput[] = [];
/** Whether the caller may approve / reject plan lines (the gateway's decision gate). */
export let CAN_DECIDE = false;

/** Record a verdict on a plan line locally after the gateway accepted it. */
export function setDecision(movementId: string, decision: Movement['decision']): void {
  const m = MOVEMENTS.find(x => x.id === movementId);
  if (m) m.decision = decision;
}

const _byId = (xs: any[]): Record<string, any> =>
  Object.fromEntries((xs || []).map((x: any) => [x.id, x]));

/** Reassign the live bindings from a `/gw/ct/plan` payload (shape mirrors plan.ts). */
export function applyPlan(p: any): void {
  if (!p || typeof p !== 'object') return;
  if (p.regions) REGIONS = p.regions;
  if (p.regionNames) REGION_NAMES = p.regionNames;
  if (p.periods) PERIODS = p.periods;
  if (p.currentPeriod) CURRENT_PERIOD = p.currentPeriod;
  if (p.commodities) COMMODITIES = p.commodities;
  if (p.warehouses) { WAREHOUSES = p.warehouses; WH_BY_ID = _byId(WAREHOUSES); }
  if (p.nodes) { NODES = p.nodes; NODE_BY_ID = _byId(NODES); }
  if (p.movements) MOVEMENTS = p.movements;
  if (p.vehicleRuns) { VEHICLE_RUNS = p.vehicleRuns; RUN_BY_ID = _byId(VEHICLE_RUNS); }
  if (p.selectedId) SELECTED_ID = p.selectedId;
  if (p.periodStats) PERIOD_STATS = p.periodStats;
  if (p.summary) SUMMARY = p.summary;
  if (p.baseline) BASELINE = p.baseline;
  if (p.kpis) KPIS = p.kpis;
  if (typeof p.canDecide === 'boolean') CAN_DECIDE = p.canDecide;
}

let _load: Promise<boolean> | null = null;
/** Fetch the mart-backed plan. Every caller awaits the same in-flight request, so no
 *  caller sees "loaded" before the data arrived: but a FAILED attempt is never
 *  cached (a page opened mid-deploy must recover on the next mount or retry),
 *  and one automatic retry covers a gateway that was restarting. */
export function ensurePlanLoaded(): Promise<boolean> {
  if (_load) return _load;
  const attempt = async (): Promise<boolean> => {
    const r = await authFetch('/gw/ct/plan');
    if (!r.ok) throw new Error(String(r.status));
    applyPlan(await r.json());
    return true;
  };
  _load = (async () => {
    try {
      return await attempt();
    } catch {
      await new Promise(res => setTimeout(res, 5000));
      try {
        return await attempt();
      } catch {
        _load = null;    // do not cache failure: the page keeps its empty state
        return false;
      }
    }
  })();
  return _load;
}
