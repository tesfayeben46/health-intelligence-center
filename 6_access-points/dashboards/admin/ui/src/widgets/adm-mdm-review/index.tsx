/**
 * MDM stewardship: the human half of master-data conformance.
 *
 * The matcher (2_pipelines/mdm) proposes same-entity candidates with scores
 * and evidence (names, admin area, distance); a steward accepts or rejects
 * here. Verdicts land in the append-only bitemporal decision log that the
 * vault's resolution views read LIVE, so a verdict re-keys the source record
 * on the next read; the insert-only hubs/links record it on the next build.
 * Reverting supersedes (recorded_to) and re-opens the candidate: proven live
 * to restore the previous conformed keys byte for byte.
 *
 * Page shell: an underline tab strip under the page title:
 *   Review matches  the queue with each suggestion's source records side by
 *                   side; a verdict is a proposal (ReviewTab.tsx)
 *   Explore         the governed lists, one list, one record and its history
 *                   (ExploreTab.tsx)
 *   Approvals       proposals made by someone else, approve or decline
 *                   (Proposals.tsx)
 *   Decisions       my proposals, what needs me, and the decision log
 *   Publish         master-data releases
 *   Setup           sources and matching rules, read-only (SetupTab.tsx)
 * Maker-checker: a steward proposes, a different person approves, and only an
 * approval writes the decision log (routes/mdm_approvals.py).
 * Tabs are listed only when the caller's resolved catalog grants the widget
 * behind them and the person holds the part they need (steward work vs
 * approval: from the spec's role codes and capabilities; the gateway checks
 * every call again). The active tab is mirrored in the URL hash (#mdm/<tab>).
 * Every value is read from the gateway; an unreachable gateway
 * degrades to a clear error note.
 */
import { useCallback, useEffect, useState } from 'react';
import * as mdm from '../../api/mdm';
import { useTableKit, TableToolbar, TablePager, SortTh } from '../../kit/tableKit';
import type { AdminWidget } from '../../runtime/types';
import { useDashboardSpec, specAllowsWidget } from '../../api/spec';
import { ActionError, DoneNote, LoadError } from './parts';
import ReviewTab from './ReviewTab';
import ExploreTab from './ExploreTab';
import SetupTab from './SetupTab';
import { ApprovalsTab, MyProposals } from './Proposals';
import '../../styles/review.css';
import '../../styles/mdm.css';

function DecisionsTab({ refreshKey }: { refreshKey: number }) {
  const [rows, setRows] = useState<mdm.Decision[] | null>(null);
  const [history, setHistory] = useState(false);
  const [err, setErr] = useState('');

  const load = useCallback(() => {
    mdm.decisions(!history)
      .then(r => { setRows(r.decisions); setErr(''); })
      .catch(e => setErr(String(e?.message || e)));
  }, [history]);
  useEffect(load, [load, refreshKey]);

  const [actErr, setActErr] = useState('');
  const [sent, setSent] = useState('');
  // An undo is a proposal another person approves (no one-step revert).
  async function undo(d: mdm.Decision) {
    setActErr(''); setSent('');
    try {
      const r = await mdm.proposeRestore(d.decision_id);
      setSent(r.status === 'checked' ? 'Undo sent for approval.' : 'Undo not sent: the check failed.');
      load();
    }
    catch (e: any) { setActErr(String(e?.message || e)); }
  }

  const kit = useTableKit(rows ?? [], {
    pageSize: 12,
    search: d => `${d.source_system_code} ${d.source_record_key} ${d.master_bk ?? ''} ${d.decided_by}`,
    sortVals: {
      when:    d => d.recorded_from,
      record:  d => `${d.source_system_code}/${d.source_record_key}`,
      verdict: d => d.decision,
    },
    initialSort: { key: 'when', dir: 'desc' },
  });

  return (
    <>
      <div className="section-hdr">
        <label className="muted text-xs">
          <input type="checkbox" checked={history} onChange={e => setHistory(e.target.checked)} />
          {' '}show superseded history
        </label>
      </div>
      {actErr && <ActionError text="Could not save the decision." err={actErr} />}
      {sent && <DoneNote text={sent} onClose={() => setSent('')} />}
      {err && <LoadError what="decisions" err={err} retry={load} />}
      {rows === null && !err && <p className="muted">Loading decisions…</p>}
      {rows !== null && rows.length === 0 && !err &&
        <p className="muted">No decisions in this view.</p>}
      {rows !== null && rows.length > 0 && (
        <>
          <TableToolbar kit={kit} placeholder="Search record, master, steward…" unit="decisions" />
          <div className="tk-scroll">
            <table className="dash">
              <thead>
                <tr>
                  <SortTh kit={kit} k="record">Source record</SortTh>
                  <SortTh kit={kit} k="verdict">Verdict</SortTh>
                  <th>Master</th>
                  <th>Steward</th>
                  <SortTh kit={kit} k="when">Recorded</SortTh>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {kit.pageRows.map(d => (
                  <tr key={d.decision_id} className={d.current ? undefined : 'muted'}>
                    <td>
                      <div className="v">{d.source_system_code}</div>
                      <div className="muted text-xs">{d.source_record_key.slice(0, 16)}…</div>
                    </td>
                    <td><span className={`pill ${d.decision === 'accept' ? 'good' : 'alert'}`}>{d.decision}</span>
                        {!d.current && <span className="muted text-xs"> superseded</span>}</td>
                    <td className="v">{d.master_bk ?? 'n/a'}</td>
                    <td className="text-sm">{d.decided_by}</td>
                    <td className="text-sm">{new Date(d.recorded_from).toLocaleString()}</td>
                    <td>{d.current &&
                      <button className="btn btn-sm btn-ghost" onClick={() => undo(d)}>Propose undo</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <TablePager kit={kit} />
        </>
      )}
    </>
  );
}


/** Master-data releases: named, reproducible cuts of who the masters were.
 *  A release pins both coordinates that determine a master: the decision-log
 *  timestamp and the source watermark: because either one moving changes the
 *  answer. Published releases are immutable; correcting one means publishing a
 *  successor. */
function ReleasesTab() {
  const [rows, setRows] = useState<mdm.Release[] | null>(null);
  const [err, setErr] = useState('');
  const [name, setName] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState('');

  const load = useCallback(() => {
    mdm.releases()
      .then(r => { setRows(r.releases); setErr(''); })
      .catch(e => setErr(String(e?.message || e)));
  }, []);
  useEffect(load, [load]);

  const [actErr, setActErr] = useState('');
  async function publish() {
    if (!name.trim()) return;
    setBusy(true);
    setActErr('');
    try {
      const r = await mdm.publishRelease(name.trim(), note.trim() || undefined);
      setFlash(`Published ${r.release_id}: ${r.stats.masters} masters pinned`);
      setName(''); setNote(''); load();
    } catch (e: any) { setActErr(String(e?.message || e)); }
    finally { setBusy(false); }
  }

  const kit = useTableKit(rows ?? [], {
    pageSize: 10,
    search: r => `${r.release_id} ${r.created_by} ${r.note ?? ''} ${r.status}`,
    sortVals: { release: r => r.release_id, when: r => r.created_at, status: r => r.status },
    initialSort: { key: 'when', dir: 'desc' },
  });

  return (
    <>
      <div className="section-hdr">
        <div>
          <input value={name} onChange={e => setName(e.target.value)}
                 placeholder="release id (e.g. R-2026-09)" />{' '}
          <input value={note} onChange={e => setNote(e.target.value)}
                 placeholder="what it is for (optional)" />{' '}
          <button className="btn btn-sm" disabled={busy || !name.trim()} onClick={publish}>
            Publish release
          </button>
        </div>
        {flash && <span className="muted text-xs">{flash}</span>}
      </div>
      {actErr && <ActionError text="Release not published. Review the error and try again." err={actErr} />}
      {err && <LoadError what="releases" err={err} retry={load} />}
      {rows === null && !err && <p className="muted">Loading releases…</p>}
      {rows !== null && rows.length === 0 && !err &&
        <p className="muted">No releases have been published yet.</p>}
      {rows !== null && rows.length > 0 && (
        <>
          <TableToolbar kit={kit} placeholder="Search release, steward, note…" unit="releases" />
          <div className="tk-scroll">
            <table className="dash">
              <thead>
                <tr>
                  <SortTh kit={kit} k="release">Release</SortTh>
                  <SortTh kit={kit} k="status">Status</SortTh>
                  <th>Pinned coordinates</th>
                  <th>Masters</th>
                  <SortTh kit={kit} k="when">Published</SortTh>
                </tr>
              </thead>
              <tbody>
                {kit.pageRows.map(r => (
                  <tr key={r.release_id}>
                    <td>
                      <div className="v">{r.release_id}</div>
                      <div className="muted text-xs">{r.note ?? ''}</div>
                    </td>
                    <td><span className={`pill ${r.status === 'published' ? 'good' : 'info'}`}>{r.status}</span>
                        {r.superseded_by && <div className="muted text-xs">Superseded by {r.superseded_by}</div>}</td>
                    <td className="muted text-xs">
                      decisions {new Date(r.decisions_as_of).toLocaleString()}<br />
                      sources {new Date(r.source_watermark).toLocaleString()}
                    </td>
                    <td className="v">{r.stats?.masters ?? 'n/a'}
                      <div className="muted text-xs">
                        {r.stats?.conformed ?? 0} conformed · {r.stats?.decisions_applied ?? 0} decisions
                      </div>
                    </td>
                    <td className="text-sm">{r.created_by}<br />
                      <span className="muted text-xs">
                        {r.published_at ? new Date(r.published_at).toLocaleString() : 'n/a'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <TablePager kit={kit} />
        </>
      )}
    </>
  );
}

type TabKey = 'review' | 'explore' | 'approvals' | 'decisions' | 'publish' | 'setup';
type Part = 'steward' | 'approver' | 'either';

/* The workspace tabs. `widget` is the catalog widget each tab needs (a tab is
   listed only when the caller's resolved spec grants it); `part` is the half
   of maker-checker it serves. All live in adm-mdm-review today. */
const TABS: Array<{ key: TabKey; label: string; widget: string; part: Part }> = [
  { key: 'review',    label: 'Review matches', widget: 'adm-mdm-review', part: 'steward' },
  { key: 'explore',   label: 'Explore',        widget: 'adm-mdm-review', part: 'either' },
  { key: 'approvals', label: 'Approvals',      widget: 'adm-mdm-review', part: 'approver' },
  { key: 'decisions', label: 'Decisions',      widget: 'adm-mdm-review', part: 'steward' },
  { key: 'publish',   label: 'Publish',        widget: 'adm-mdm-review', part: 'steward' },
  { key: 'setup',     label: 'Setup',          widget: 'adm-mdm-review', part: 'either' },
];

/* Mirrors the gateway: stewards = mdm.STEWARD_ROLES; approvers = the
   mdm_approve capability (or R-SUPER). Only hides tabs: every call is
   checked again server-side. */
const STEWARD_CODES = ['R-STEW', 'R-OPS-ADM', 'R-SUPER'];
function parts(spec: { role_codes?: string[]; capabilities?: string[] } | null) {
  const roles = spec?.role_codes ?? [];
  const steward = roles.some(r => STEWARD_CODES.includes(r));
  const approver = (spec?.capabilities ?? []).includes('mdm_approve') || roles.includes('R-SUPER');
  return { steward, approver };
}

function DecisionsTabs({ refreshKey }: { refreshKey: number }) {
  const [view, setView] = useState<'mine' | 'attention' | 'log'>('mine');
  return (
    <>
      <div className="seg mdm-seg" role="group" aria-label="Decisions view">
        {([['mine', 'My proposals'], ['attention', 'Needs my attention'], ['log', 'Decision log']] as const).map(([k, l]) => (
          <button key={k} className={view === k ? 'active' : undefined} aria-pressed={view === k}
                  onClick={() => setView(k)}>{l}</button>
        ))}
      </div>
      {view === 'log'
        ? <div className="card card-elev"><DecisionsTab refreshKey={refreshKey} /></div>
        : <MyProposals view={view} refreshKey={refreshKey} />}
    </>
  );
}

const HASH_PREFIX = '#mdm/';

function tabFromHash(): TabKey | null {
  const h = window.location.hash;
  if (!h.startsWith(HASH_PREFIX)) return null;
  const key = h.slice(HASH_PREFIX.length).split('/')[0];
  return TABS.some(t => t.key === key) ? key as TabKey : null;
}

function MdmReview() {
  const { spec, loaded, denied } = useDashboardSpec('admin');
  const who = parts(spec);
  const tabs = loaded ? TABS.filter(t => specAllowsWidget(spec, 'admin/mdm', t.widget)
    && (t.part === 'either' || (t.part === 'steward' ? who.steward : who.approver))) : [];
  const [tab, setTab] = useState<TabKey>(() => tabFromHash() ?? 'review');
  const [decidedTick, setDecidedTick] = useState(0);
  const [openBk, setOpenBk] = useState('');     // a master opened from Review

  const active = tabs.find(t => t.key === tab) ?? tabs[0];

  // Mirror the shown tab in the hash; drop it when the page is left so the
  // next page does not inherit an #mdm/ link.
  useEffect(() => {
    if (active) history.replaceState(null, '', HASH_PREFIX + active.key);
  }, [active?.key]);
  useEffect(() => () => {
    if (window.location.hash.startsWith('#mdm')) {
      history.replaceState(null, '', window.location.pathname + window.location.search);
    }
  }, []);

  const openMaster = useCallback((bk: string) => { setOpenBk(bk); setTab('explore'); }, []);

  return (
    <div className="mdm-workspace">
      {tabs.length > 0 && <nav className="tab-nav" aria-label="MDM Stewardship sections">
        {tabs.map(t => (
          <a key={t.key} className={t.key === active?.key ? 'active' : undefined}
             aria-current={t.key === active?.key ? 'page' : undefined}
             href={HASH_PREFIX + t.key}
             onClick={e => { e.preventDefault(); setTab(t.key); }}>
            {t.label}
          </a>
        ))}
      </nav>}
      {loaded && !active && (
        <div className="rv-note rv-note-alert" role="alert">
          <svg className="ico ico-sm"><use href="#i-alert" /></svg>
          <span className="rv-note-txt">
            {denied || spec ? 'You do not have access to MDM Stewardship.' : 'Could not load MDM Stewardship.'}
          </span>
        </div>
      )}
      {active && (
        <div className="page-section">
          {active.key === 'review' &&
            <ReviewTab onProposed={() => setDecidedTick(t => t + 1)} onOpenMaster={openMaster} />}
          {active.key === 'explore' && <ExploreTab openBk={openBk} onOpened={() => setOpenBk('')} />}
          {active.key === 'approvals' && <ApprovalsTab onChanged={() => setDecidedTick(t => t + 1)} />}
          {active.key === 'decisions' && <DecisionsTabs refreshKey={decidedTick} />}
          {active.key === 'publish' && <div className="card card-elev"><ReleasesTab /></div>}
          {active.key === 'setup' && <SetupTab />}
        </div>
      )}
    </div>
  );
}

const widget: AdminWidget = {
  id: 'adm-mdm-review',
  Component: MdmReview,
};
export default widget;
