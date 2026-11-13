import { useState } from 'react';
import { WH_BY_ID, NODE_BY_ID, RUN_BY_ID, CAN_DECIDE, setDecision, type Movement } from '../data/runtime';
import { authFetch } from '../../api/useAuth';
import { bumpView } from '../store';
import { rankedShipments, type Slicer } from '../lib/select';
import { IconArrowRight, IconChevronD, IconStockShort, IconRunout, IconCover,
  IconPallet, IconArrowDown, IconEquals, IconPlus, IconCheck } from '../icons';

const SHIP_PREVIEW = 7; // rows shown before "show all"

const fmt = (n: number) => Math.round(n).toLocaleString('en-US');
const signed = (n: number) => (n > 0 ? `+${fmt(n)}` : fmt(n));
const hhmm = (min: number) => `${String(Math.floor(min / 60) % 24).padStart(2, '0')}:${String(Math.round(min) % 60).padStart(2, '0')}`;

/** One step of the sizing walk. The marker states what the row does to the running
 *  balance; the value is tinted by its SIGN, not by which row it happens to be, so
 *  a facility that is not actually short does not render a red "projected before". */
function Step({ Icon, label, value, signedVal, total }:
  { Icon: (p: any) => JSX.Element; label: string; value: number; signedVal?: boolean; total?: boolean }) {
  const tone = !signedVal ? '' : value < 0 ? ' neg' : ' pos';
  return (
    <li className={`ct-step${total ? ' total' : ''}`}>
      <span className="ct-step-mark"><Icon /></span>
      <span className="ct-step-lbl">{label}</span>
      <span className={`ct-step-val${tone}`}>{signedVal ? signed(value) : fmt(value)}</span>
    </li>
  );
}

interface Props { movement: Movement; slicer: Slicer; onSelect: (id: string) => void }

const DEMAND_BASIS: Record<string, string> = {
  forecast: 'Forecast', elmis_amc: 'eLMIS AMC', forecast_and_amc: 'Forecast and eLMIS AMC',
};

export default function MovementPanel({ movement, slicer, onSelect }: Props) {
  const [shipOpen, setShipOpen] = useState(true);
  const [shipAll, setShipAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const verdict = movement.decision?.decision ?? null;
  const decidedOn = movement.decision ? new Date(movement.decision.at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '';
  const decide = async (d: 'approve' | 'reject' | 'undo') => {
    setBusy(true); setErr(null);
    try {
      const r = d === 'undo'
        ? await authFetch(`/gw/ct/decisions/${movement.decision!.id}/revert`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        : await authFetch(`/gw/ct/plan/lines/${encodeURIComponent(movement.id)}/decide`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ decision: d }) });
      if (!r.ok) { setErr(r.status === 403 ? 'Not permitted' : 'Not saved. Try again.'); return; }
      const j = await r.json();
      setDecision(movement.id, d === 'undo' ? null : { id: j.id, decision: j.decision, by: j.by, at: j.at });
      bumpView();
    } catch { setErr('Not saved. Try again.'); } finally { setBusy(false); }
  };
  const w = WH_BY_ID[movement.fromId];
  const n = NODE_BY_ID[movement.toId];
  const why = movement.why;
  const list = rankedShipments(slicer);

  // derived, plain-language figures (per METRICS-AUDIT.md)
  const run = movement.vehicleRunId ? RUN_BY_ID[movement.vehicleRunId] : null;
  const stop = run?.stops.find(st => st.nodeId === movement.toId);
  const shortfall = why.prePlan < 0 ? -why.prePlan : 0;
  const coverAfter = movement.amc ? why.postPlan / movement.amc : null; // months of cover after delivery
  const overflow = movement.reason === 'capacity-overflow';
  const nearest = WH_BY_ID[movement.nearestHubId];

  return (
    <div className="ct-rail">
      <div className={`ct-selcard${verdict === 'reject' ? ' rejected' : ''}`}>
        <div className="ct-sel-top">
          <span className="ct-eyebrow">Selected movement</span>
          <span className={`ct-sel-planned ${!movement.planned ? 'warn' : verdict === 'approve' ? 'ok' : verdict === 'reject' ? 'off' : 'pending'}`}>
            {!movement.planned ? 'Not covered' : verdict === 'approve' ? 'Approved' : verdict === 'reject' ? 'Rejected' : 'Planned'}
          </span>
        </div>

        <div className="ct-sel-from">{w.name}</div>
        <div className="ct-sel-to">
          <h2>{n.name}</h2>
          <IconArrowRight className="arrow" />
        </div>

        <div className="ct-sel-units">{fmt(movement.units)} <span className="ct-sel-unit">units</span></div>
        <div className="ct-sel-drug">{movement.drug}</div>

        {/* The three numbers a planner acts on. Replaces a sentence that restated
            values already shown in the headline and the sizing table below. */}
        <div className="ct-trio">
          <div className="ct-trio-i">
            <IconStockShort />
            <span className="ct-trio-v">{fmt(shortfall)}</span>
            <span className="ct-trio-l">Short this month</span>
          </div>
          <div className="ct-trio-i">
            <IconRunout />
            <span className="ct-trio-v">{movement.daysToStockout != null ? `${movement.daysToStockout}d` : 'n/a'}</span>
            <span className="ct-trio-l">Until stock out</span>
          </div>
          <div className="ct-trio-i">
            <IconCover />
            <span className="ct-trio-v">{coverAfter != null ? coverAfter.toFixed(1) : 'n/a'}</span>
            <span className="ct-trio-l">Months cover after</span>
          </div>
        </div>

        <div className="ct-facts">
          {run && <div className="ct-fact"><span className="k">{run.vehicleType == null ? 'Delivery stop' : 'Delivery'}</span><span className="v">{run.vehicleType == null ? `Stop ${movement.sequenceIndex} of ${run.stops.length}` : `${run.id}, stop ${movement.sequenceIndex} of ${run.stops.length}`}</span></div>}
          {stop && stop.etaMin != null && <div className="ct-fact"><span className="k">Delivery ETA</span><span className="v">{hhmm(stop.etaMin)}</span></div>}
          <div className="ct-fact"><span className="k">{run && run.vehicleType == null ? 'Straight-line distance' : 'Road distance'}</span><span className="v">{fmt(movement.laneKm)} km</span></div>
          {movement.demandBasis && <div className="ct-fact"><span className="k">Demand basis</span><span className="v">{DEMAND_BASIS[movement.demandBasis] ?? movement.demandBasis}</span></div>}
          {movement.forecastP10 != null && movement.forecastP90 != null && (
            <div className="ct-fact" title={`Backtested model, version ${movement.forecastVersion ?? ''} · MLflow run ${(movement.forecastRun ?? '').slice(0, 12)} · for ${movement.period}`}>
              <span className="k">Forecast range</span>
              <span className="v">{fmt(movement.forecastP10)} to {fmt(movement.forecastP90)}</span>
            </div>
          )}
          {overflow && <div className="ct-fact"><span className="k">Rerouted from</span><span className="v">{nearest.short}, +{fmt(movement.assignDeltaKm)} km</span></div>}
        </div>

        {CAN_DECIDE && movement.planned && (
          <div className="ct-decide">
            {verdict
              ? <>
                  <span className="ct-decide-meta">{verdict === 'approve' ? 'Approved' : 'Rejected'} by {movement.decision!.by}, {decidedOn}</span>
                  <button className="ct-decide-btn" disabled={busy} onClick={() => decide('undo')}>Revert</button>
                </>
              : <>
                  <button className="ct-decide-btn primary" disabled={busy} onClick={() => decide('approve')}>Approve</button>
                  <button className="ct-decide-btn" disabled={busy} onClick={() => decide('reject')}>Reject</button>
                </>}
            {err && <span className="ct-decide-err">{err}</span>}
          </div>
        )}

        <div className="ct-why-hdr">How this shipment was sized</div>
        <ol className="ct-steps">
          <Step Icon={IconPallet}    label="Stock on hand"     value={why.opening} />
          <Step Icon={IconArrowDown} label="Demand this month" value={why.signal}  signedVal />
          <Step Icon={IconEquals}    label="Projected before"  value={why.prePlan} signedVal />
          <Step Icon={IconPlus}      label="Shipping in"       value={why.inbound} signedVal />
          <Step Icon={IconCheck}     label="Projected after"   value={why.postPlan} signedVal total />
        </ol>
      </div>

      <div className="ct-shipcard">
        <div className="ct-ship-hdr">
          <span className="ct-eyebrow">Recommended shipments</span>
          <div className="grp">
            <span className="ct-ship-count">{list.length}</span>
            <button className="ct-collapse" aria-expanded={shipOpen} aria-label={shipOpen ? 'Collapse list' : 'Expand list'}
              onClick={() => setShipOpen(o => !o)}><IconChevronD /></button>
          </div>
        </div>
        {shipOpen && <>
        <div className="ct-shiplist">
          {(shipAll ? list : list.slice(0, SHIP_PREVIEW)).map(m => {
            const dn = NODE_BY_ID[m.toId];
            const active = m.id === movement.id;
            return (
              <button key={m.id} className={`ct-ship${active ? ' active' : ''}`} onClick={() => onSelect(m.id)}>
                <span className="ct-ship-prio">{m.daysToStockout != null ? `${m.daysToStockout}d` : 'n/a'}</span>
                <span className="ct-ship-main">
                  <span className="ct-ship-fac">{dn.name}</span>
                  <span className="ct-ship-sub">{WH_BY_ID[m.fromId].short}, {m.drugShort}</span>
                </span>
                <span className="ct-ship-units">{fmt(m.units)}</span>
              </button>
            );
          })}
        </div>
        {list.length > SHIP_PREVIEW && (
          <button className="ct-shipmore" onClick={() => setShipAll(a => !a)}>
            {shipAll ? `Show top ${SHIP_PREVIEW}` : `Show all ${list.length}`}
          </button>
        )}
        </>}
      </div>
    </div>
  );
}
