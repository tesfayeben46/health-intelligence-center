/** Shared slicer + selection + dynamic-metric helpers (map, rail, dispatch). */
import { MOVEMENTS, NODE_BY_ID, RUN_BY_ID, CURRENT_PERIOD,
  type Movement, type VehicleRun } from '../data/runtime';

export interface Slicer { hub: string; region: string; commodity: string; period: string }
/** Default view is scoped to the current period, not to every period at once. Read at
 *  call time: the period comes from the loaded plan, not from module load. */
export const allSlicer = (): Slicer => ({ hub: 'all', region: 'all', commodity: 'all', period: CURRENT_PERIOD });

export function matches(m: Movement, s: Slicer): boolean {
  if (s.period !== 'all' && m.period !== s.period) return false;
  if (s.hub !== 'all' && m.fromId !== s.hub) return false;
  if (s.region !== 'all' && NODE_BY_ID[m.toId]?.region !== s.region) return false;
  if (s.commodity !== 'all' && m.drugShort !== s.commodity) return false;
  return true;
}

export const filterMovements = (s: Slicer): Movement[] => MOVEMENTS.filter(m => matches(m, s));

/** Recommended shipments, most urgent first. */
export const rankedShipments = (s: Slicer): Movement[] =>
  filterMovements(s).filter(m => m.planned).sort((a, b) =>
    b.priority - a.priority || (a.daysToStockout ?? Infinity) - (b.daysToStockout ?? Infinity) || b.units - a.units);

/** Vehicle runs referenced by the filtered movements. */
export function touchedRuns(movs: Movement[]): VehicleRun[] {
  const ids = new Set<string>();
  for (const m of movs) if (m.vehicleRunId) ids.add(m.vehicleRunId);
  return [...ids].map(id => RUN_BY_ID[id]).filter(Boolean);
}

/** All metrics recomputed from a filtered movement set (react to slicer + selection).
 *  Unplanned movements are at-risk needs the plan leaves without an order: they count
 *  toward coverage, never toward units, deliveries or stops. Fill and cost exist only
 *  when the plan carries them (null otherwise: never shown as 0). */
export function summarize(all: Movement[]) {
  const movs = all.filter(m => m.planned);
  const units = movs.reduce((s, m) => s + m.units, 0);
  const legs = movs.length;
  const runs = touchedRuns(movs);
  // A facility is one STOP however many commodity lines it receives, so trips saved
  // compares stops against runs, not shipment lines against runs.
  const stops = new Set(movs.map(m => m.toId)).size;
  const laneKm = movs.reduce((s, m) => s + m.laneKm, 0);
  const baseKm = movs.reduce((s, m) => s + m.baselineKm, 0);
  const costs = movs.filter(m => m.costUnits != null);
  const fills = runs.filter(r => r.fillRate != null);
  const atRisk = all.filter(m => m.atRisk);
  const covered = atRisk.filter(m => m.planned).length;
  return {
    units, legs, stops,
    runCount: runs.length,
    origins: new Set(movs.map(m => m.fromId)).size,
    kmSaved: Math.max(0, Math.round(baseKm - laneKm)),
    kmSavedPct: baseKm ? Math.round((1 - laneKm / baseKm) * 100) : 0,
    fewerTrips: Math.max(0, stops - runs.length),
    avgFill: fills.length ? fills.reduce((s, r) => s + (r.fillRate as number), 0) / fills.length : null,
    cost: costs.length ? costs.reduce((s, m) => s + (m.costUnits as number), 0) : null,
    atRiskNeeds: atRisk.length,
    uncovered: atRisk.length - covered,
    coveredPct: atRisk.length ? (100 * covered) / atRisk.length : null,
    facilitiesAtRisk: new Set(atRisk.map(m => m.toId)).size,
    facilities: new Set(all.map(m => m.toId)).size,
  };
}

/** Re-exported live binding: the full list from the loaded plan (never sliced),
 *  so the dropdown does not shrink when a slice happens to lack a commodity. */
export { COMMODITIES } from '../data/runtime';
