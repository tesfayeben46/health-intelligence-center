import { Fragment, useMemo, useState } from 'react';
import { WH_BY_ID, NODE_BY_ID } from '../data/runtime';
import { filterMovements, touchedRuns, type Slicer } from '../lib/select';
import { IconTruck, IconRoute } from '../icons';

const fmt = (n: number) => Math.round(n).toLocaleString('en-US');
const hhmm = (min: number) => `${String(Math.floor(min / 60) % 24).padStart(2, '0')}:${String(Math.round(min) % 60).padStart(2, '0')}`;
const PAGE = 8;

export default function DispatchRoutes({ slicer }: { slicer: Slicer }) {
  const runs = useMemo(
    () => touchedRuns(filterMovements(slicer)).sort((a, b) => b.totalUnits - a.totalUnits),
    [slicer],
  );
  const [open, setOpen] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(runs.length / PAGE));
  const p = Math.min(page, pages - 1);
  const view = runs.slice(p * PAGE, p * PAGE + PAGE);
  // Columns render only when the plan carries the field (no vehicle, fill or cost
  // data exists for some sources: never print a NaN value).
  const hasVehicle = runs.some(r => r.vehicleType != null);
  const hasFill = runs.some(r => r.fillRate != null);
  const hasCost = runs.some(r => r.costPerUnit != null);
  const cols = 7 + (hasVehicle ? 1 : 0) + (hasFill ? 1 : 0) + (hasCost ? 1 : 0);

  return (
    <div className="ct-routecard">
      <div className="ct-routecard-hdr">
        <div className="ct-card-hd"><span className="ct-card-ico sm"><IconRoute /></span>
          <h3>Dispatch schedule</h3>
        </div>
        <span className="muted text-sm">{runs.length} runs</span>
      </div>
      <div className="ct-table-wrap">
        <table className="ct-routes">
          <thead>
            <tr>
              <th></th><th>Run</th>{hasVehicle && <th>Vehicle</th>}<th>Origin</th><th className="num">Stops</th>
              <th className="num">Units</th>{hasFill && <th className="num">Fill</th>}
              <th className="num">{hasVehicle ? 'Route km' : 'Distance km'}</th>{hasCost && <th className="num">Cost/unit</th>}<th className="num">{hasVehicle ? 'Km saved' : 'Km saved vs direct'}</th><th>Run status</th>
            </tr>
          </thead>
          <tbody>
            {view.map(r => {
              const isOpen = open === r.id;
              const pct = r.fillRate != null ? Math.round(r.fillRate * 100) : null;
              return (
                <Fragment key={r.id}>
                  <tr className={`ct-run-row${isOpen ? ' open' : ''}`} onClick={() => setOpen(isOpen ? null : r.id)}>
                    <td className="ct-caret">{isOpen ? '▾' : '▸'}</td>
                    <td><span className="ct-veh" title={r.id}><IconTruck /> {hasVehicle ? r.id : `${WH_BY_ID[r.originHubId].short} run`}</span></td>
                    {hasVehicle && <td className="muted">{r.vehicleType ?? ''}</td>}
                    <td>{WH_BY_ID[r.originHubId].short}</td>
                    <td className="num">{r.stops.length}</td>
                    <td className="num">{fmt(r.totalUnits)}</td>
                    {hasFill && <td className="num">{pct != null ? `${pct}%` : ''}</td>}
                    <td className="num">{fmt(r.totalRoadKm)}</td>
                    {hasCost && <td className="num">{r.costPerUnit != null ? r.costPerUnit.toFixed(1) : ''}</td>}
                    <td className="num">{fmt(r.kmSavedVsBaseline)}</td>
                    <td><span className={`pill ${r.status === 'Dispatched' ? 'good' : r.status === 'Loading' ? 'watch' : 'neutral'}`}>{r.status}</span></td>
                  </tr>
                  {isOpen && (
                    <tr className="ct-itin-row">
                      <td></td>
                      <td colSpan={cols}>
                        <div className="ct-itin">
                          <div className="ct-itin-dep"><span className="ct-itin-dot" />{WH_BY_ID[r.originHubId].short}{r.depart ? ` · depart ${r.depart}` : ''}</div>
                          {r.stops.map(st => (
                            <div className="ct-itin-stop" key={st.nodeId}>
                              <span className="ct-itin-seq">{st.seq}</span>
                              <span className="ct-itin-name">{NODE_BY_ID[st.nodeId].name}</span>
                              <span className="ct-itin-meta">{fmt(st.dropUnits)} units{st.legKm >= 1 ? ` · ${fmt(st.legKm)} km` : ''}{st.etaMin != null ? ` · ETA ${hhmm(st.etaMin)}` : ''}</span>
                            </div>
                          ))}
                          <div className="ct-itin-dep"><span className="ct-itin-dot" />Return {WH_BY_ID[r.originHubId].short}{r.eta ? ` · ${r.eta}` : ''}</div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="ct-pager">
          <button disabled={p === 0} onClick={() => setPage(p - 1)}>Prev</button>
          <span className="ct-pager-info">Page {p + 1} of {pages}</span>
          <button disabled={p >= pages - 1} onClick={() => setPage(p + 1)}>Next</button>
        </div>
      )}
    </div>
  );
}
