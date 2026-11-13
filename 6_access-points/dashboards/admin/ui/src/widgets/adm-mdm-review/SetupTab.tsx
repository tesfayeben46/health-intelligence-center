/**
 * Setup: what feeds master data and how matching is set, read-only
 * (GET /api/mdm/setup).
 *   Sources         each source system behind the facility list: records in
 *                   the vault, when the latest one arrived, and whether it is
 *                   still receiving
 *   Matching rules  the matcher's current rule versions (init 45 registry):
 *                   threshold, on or off, version, when it last changed
 * Rule changes are new registry versions made by a reviewed change, never
 * from this screen, so it is read-only for every role.
 */
import { useCallback, useEffect, useState } from 'react';
import * as mdm from '../../api/mdm';
import { LoadError, fmtDay, fmtNum, fmtWhen } from './parts';

export default function SetupTab() {
  const [d, setD] = useState<mdm.Setup | null>(null);
  const [err, setErr] = useState('');
  const [list, setList] = useState('all');
  const [state, setState] = useState<'any' | 'active' | 'off'>('any');

  const load = useCallback(() => {
    mdm.setup().then(r => { setD(r); setErr(''); })
      .catch(e => setErr(String(e?.message || e)));
  }, []);
  useEffect(load, [load]);

  const rules = (d?.rules ?? []).filter(r =>
    (list === 'all' || r.list === list)
    && (state === 'any' || (state === 'active') === r.enabled));
  const lists = [...new Set((d?.rules ?? []).map(r => r.list))];

  return (
    <>
      <div className="ps-hdr"><h3 className="ps-title">Setup</h3><span className="ps-meta">Read-only</span></div>
      {err && <LoadError what="Setup" err={err} retry={load} />}
      {!d && !err && <p className="muted">Loading Setup…</p>}
      {d && (
        <div className="flex-col gap-lg">
          <div className="card card-elev">
            <div className="card-hdr"><h4>Sources</h4><span className="meta">Systems that send records to master data, through the lake</span></div>
            {d.sources.length === 0
              ? <p className="muted">No source has sent records yet.</p>
              : <div className="tk-scroll">
                  <table className="dash">
                    <thead>
                      <tr><th>Source</th><th>Lists</th><th>Last received</th><th className="num">Records</th><th>Status</th></tr>
                    </thead>
                    <tbody>
                      {d.sources.map(s => (
                        <tr key={s.source_system}>
                          <td><div className="v">{s.source_system}</div></td>
                          <td>{s.lists.join(', ')}</td>
                          <td className="mdm-nowrap">{s.last_received ? fmtWhen(s.last_received) : <span className="muted">n/a</span>}</td>
                          <td className="num">{fmtNum(s.records)}</td>
                          <td><span className={`pill ${s.status === 'Receiving' ? 'good' : 'neutral'}`}>{s.status}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>}
          </div>

          <div className="card card-elev">
            <div className="card-hdr"><h4>Matching rules</h4><span className="meta">Matches at or above a rule's threshold are proposed for review together. Nothing is linked until a second person approves it.</span></div>
            {!d.registry
              ? <p className="muted">Matching rules are not set up in this environment yet.</p>
              : <>
                  <div className="local-controls">
                    <span className="lc-label">List</span>
                    <select value={list} aria-label="List" onChange={e => setList(e.target.value)}>
                      <option value="all">All lists</option>
                      {lists.map(l => <option key={l} value={l}>{l}</option>)}
                    </select>
                    <span className="lc-label">Status</span>
                    <select value={state} aria-label="Status" onChange={e => setState(e.target.value as typeof state)}>
                      <option value="any">Any status</option>
                      <option value="active">Active</option>
                      <option value="off">Off</option>
                    </select>
                  </div>
                  {rules.length === 0
                    ? <p className="muted">No rules match these filters.</p>
                    : <div className="tk-scroll">
                        <table className="dash">
                          <thead>
                            <tr><th>Rule</th><th>List</th><th className="num">Threshold</th><th>Active</th><th>Version</th><th>Updated</th></tr>
                          </thead>
                          <tbody>
                            {rules.map(r => (
                              <tr key={`${r.entity_class}/${r.rule_code}`}>
                                <td><div className="v">{r.label}</div>
                                    <div className="muted text-xs">{r.description}</div></td>
                                <td>{r.list}</td>
                                <td className="num mdm-mono">{r.threshold.toFixed(2)}</td>
                                <td><span className={`pill ${r.enabled ? 'good' : 'neutral'}`}>{r.enabled ? 'Active' : 'Off'}</span></td>
                                <td className="mdm-mono mdm-nowrap">{r.version}</td>
                                <td className="mdm-nowrap">{fmtDay(r.updated)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>}
                </>}
          </div>
        </div>
      )}
    </>
  );
}
