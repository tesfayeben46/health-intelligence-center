/**
 * Proposals: the second half of maker-checker.
 *
 *   ApprovalsTab   proposals checked and still in time, made by someone else
 *                  (GET /api/mdm/proposals?view=approvals). The approver opens
 *                  the prepared change: the same field comparison the steward
 *                  saw: and only then may approve; declining needs a reason.
 *   MyProposals    what I proposed (view=mine) or what needs me again
 *                  (view=attention: check failed, expired, declined), with
 *                  "Check again" and "Withdraw".
 *
 * Every action is the gateway's call (POST proposal/{id}/approve|decline|
 * check|withdraw); the panel offers Approve only when the gateway says this
 * person may (can_approve: never the proposer).
 */
import { useCallback, useEffect, useState } from 'react';
import * as mdm from '../../api/mdm';
import Comparison from './Comparison';
import { ActionError, DoneNote, Empty, LoadError, RULE_LABEL, fmtWhen } from './parts';

const STATUS: Record<mdm.ProposalStatus, [string, string]> = {
  proposed:     ['info', 'Checking'],
  checked:      ['info', 'Ready for approval'],
  check_failed: ['alert', 'Check failed'],
  expired:      ['watch', 'Check expired'],
  applied:      ['good', 'Done'],
  declined:     ['neutral', 'Declined'],
  withdrawn:    ['neutral', 'Withdrawn'],
};

const KIND: Record<mdm.Proposal['kind'], string> = {
  link: 'Link records',
  distinct: 'Keep separate',
  restore: 'Undo a decision',
};

const VERDICT: Record<mdm.Proposal['kind'], string> = {
  link: 'Same',
  distinct: 'Different',
  restore: 'Undo the current decision',
};

// only the states that need the proposer to act get a line
const ACTION: Partial<Record<mdm.ProposalStatus, string>> = {
  check_failed: 'Records changed. Check again.',
  expired: 'Check expired. Check again.',
};

// history rows: event to [pill class, label]
const EVENT: Record<string, [string, string]> = {
  proposed: ['info', 'Proposed'],
  checked: ['info', 'Checked'],
  check_failed: ['alert', 'Check failed'],
  approved: ['good', 'Approved and applied'],
  declined: ['neutral', 'Declined'],
  withdrawn: ['neutral', 'Withdrawn'],
};

const noun = (ec: string) => ec.charAt(0).toUpperCase() + ec.slice(1);

function title(p: mdm.Proposal): string {
  const f = (p.evidence?.match_features ?? {}) as Record<string, unknown>;
  return String(f.source_name ?? f.norm_name ?? `${p.source_system_code} record`);
}

function StatusPill({ s }: { s: mdm.ProposalStatus }) {
  return <span className={`pill ${STATUS[s][0]}`}>{STATUS[s][1]}</span>;
}

/** The proposal's trail in the console's review-history table. The check runs
 *  automatically when a proposal is made; approve and apply are one step. */
function History({ d }: { d: mdm.ProposalDetail }) {
  const rows = d.events.filter(e => e.event !== 'applied' && EVENT[e.event]);
  return (
    <div className="rv-table-scroll">
      <table className="dash rv-table">
        <thead><tr><th>Step</th><th>By</th><th>When</th><th>Note</th></tr></thead>
        <tbody>
          {rows.map((e, i) => (
            <tr key={i}>
              <td><span className={`pill ${EVENT[e.event][0]}`}>{EVENT[e.event][1]}</span></td>
              <td>{e.event === 'checked' || e.event === 'check_failed' ? 'Automatic' : e.actor}</td>
              <td className="text-sm">{fmtWhen(e.at)}</td>
              <td className="text-sm">{e.event === 'declined' ? (d.review_note ?? 'n/a')
                : e.event === 'proposed' ? (d.note ?? 'n/a') : 'n/a'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProposalPanel({ id, mode, onChanged }:
    { id: string; mode: 'approver' | 'proposer'; onChanged: (msg: string) => void }) {
  const [d, setD] = useState<mdm.ProposalDetail | null>(null);
  const [err, setErr] = useState('');
  const [reviewed, setReviewed] = useState(false);
  const [cmp, setCmp] = useState<mdm.Comparison | null>(null);
  const [declining, setDeclining] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [actErr, setActErr] = useState('');

  const load = useCallback(() => {
    let live = true;
    setErr(''); setD(null); setReviewed(false); setCmp(null); setDeclining(false); setReason('');
    mdm.proposal(id).then(r => { if (live) setD(r); })
      .catch(e => { if (live) setErr(String(e?.message || e)); });
    return () => { live = false; };
  }, [id]);
  useEffect(load, [load]);

  async function review() {
    setReviewed(true);
    if (d?.candidate_id && d.entity_class_code === 'facility') {
      try { setCmp(await mdm.compare(d.candidate_id)); } catch { setCmp(null); }
    }
  }

  async function act(run: () => Promise<string>) {
    setBusy(true); setActErr('');
    try { onChanged(await run()); }
    catch (e: any) { setActErr(String(e?.message || e)); setBusy(false); }
  }

  if (err) return <LoadError what="this proposal" err={err} retry={load} />;
  if (!d) return <p className="muted">Loading proposal…</p>;
  const ev = d.evidence ?? {};
  const target = d.kind === 'link'
    ? <>Joins master <span className="mdm-mono">{d.master_bk}</span></>
    : d.kind === 'distinct'
      ? <>Stays separate from <span className="mdm-mono">{d.master_bk}</span></>
      : <>The current decision is undone; the record returns to automatic matching</>;
  return (
    <>
      <div className="card-hdr"><span className="meta">{KIND[d.kind]} · {noun(d.entity_class_code)}</span></div>
      <div className="mdm-q">
        <h3 className="rv-detail-name mdm-name">{title(d)}</h3>
        <StatusPill s={d.status} />
      </div>
      <p className="rv-detail-desc">Proposed by {d.proposed_by} · {fmtWhen(d.proposed_at)}</p>
      {mode === 'proposer' && ACTION[d.status] && <p className="rv-detail-desc">{ACTION[d.status]}</p>}

      <div className="rv-section-h mdm-section">
        {d.kind === 'restore' ? 'Undo a decision' : `Are these the same ${d.entity_class_code}?`}
      </div>
      <dl className="mdm-kv">
        <dt>Verdict</dt><dd>{VERDICT[d.kind]}{d.kind !== 'restore' ? ` ${d.entity_class_code}` : ''}</dd>
        {ev.score != null && <><dt>Score</dt><dd className="mdm-mono">{Number(ev.score).toFixed(2)}</dd></>}
        {ev.match_rule && <><dt>Rule</dt><dd>{RULE_LABEL[ev.match_rule] ?? ev.match_rule}</dd></>}
        {ev.high_confidence_score != null &&
          <><dt>Auto-link score</dt><dd className="mdm-mono">{Number(ev.high_confidence_score).toFixed(2)}</dd></>}
        {d.valid_until && d.status === 'checked' && <><dt>Valid until</dt><dd>{fmtWhen(d.valid_until)}</dd></>}
      </dl>

      {mode === 'approver' && d.can_approve && !reviewed &&
        <div className="rv-actions mdm-section">
          <button className="btn btn-primary" onClick={review}>Review prepared change</button>
        </div>}
      {reviewed && (
        <>
          <div className="rv-section-h mdm-section">Prepared change</div>
          <p className="rv-detail-desc">{target}</p>
          {cmp && <Comparison cmp={cmp} />}
        </>
      )}

      {mode === 'approver' && d.can_approve && reviewed && (
        <div className="mdm-confirm">
          {!declining
            ? <div className="rv-actions">
                <button className="btn btn-good" disabled={busy}
                        onClick={() => act(async () => { await mdm.approve(d.proposal_id); return 'Decision approved.'; })}>
                  <svg className="ico ico-sm"><use href="#i-check-circle" /></svg> Approve
                </button>
                <button className="btn" disabled={busy} onClick={() => setDeclining(true)}>
                  <svg className="ico ico-sm"><use href="#i-x-circle" /></svg> Decline
                </button>
              </div>
            : <div className="rv-form">
                <div className="field-group">
                  <label htmlFor={`reason-${d.proposal_id}`}>Reason for declining</label>
                  <textarea id={`reason-${d.proposal_id}`} value={reason} onChange={e => setReason(e.target.value)} />
                </div>
                <div className="rv-actions">
                  <button className="btn btn-danger" disabled={busy || !reason.trim()}
                          onClick={() => act(async () => { await mdm.decline(d.proposal_id, reason.trim()); return 'Decision declined.'; })}>
                    Confirm decline
                  </button>
                  <button className="btn btn-ghost" disabled={busy} onClick={() => setDeclining(false)}>Cancel</button>
                </div>
              </div>}
        </div>
      )}

      {mode === 'proposer' && (d.status === 'expired' || d.status === 'check_failed' || d.can_withdraw) && (
        <div className="rv-actions mdm-section">
          {(d.status === 'expired' || d.status === 'check_failed') &&
            <button className="btn" disabled={busy}
                    onClick={() => act(async () => {
                      const r = await mdm.recheck(d.proposal_id);
                      return r.status === 'checked' ? 'Checked again. Waiting for approval.' : 'The check failed again.';
                    })}>Check again</button>}
          {d.can_withdraw &&
            <button className="btn btn-ghost" disabled={busy}
                    onClick={() => act(async () => { await mdm.withdraw(d.proposal_id); return 'Proposal withdrawn.'; })}>Withdraw</button>}
        </div>
      )}
      {actErr && <ActionError text="Could not save. Review the proposal and try again." err={actErr} />}

      <div className="rv-section-h mdm-section">History</div>
      <History d={d} />
    </>
  );
}

function ProposalList({ view, mode, empty, what, refreshKey, onChanged }:
    { view: 'approvals' | 'mine' | 'attention'; mode: 'approver' | 'proposer'; empty: string;
      what: string; refreshKey?: number; onChanged?: () => void }) {
  const [rows, setRows] = useState<mdm.Proposal[] | null>(null);
  const [err, setErr] = useState('');
  const [selId, setSelId] = useState('');
  const [flash, setFlash] = useState('');

  const load = useCallback(() => {
    mdm.proposals(view).then(r => { setRows(r.proposals); setErr(''); })
      .catch(e => setErr(String(e?.message || e)));
  }, [view]);
  useEffect(load, [load, refreshKey]);

  const sel = rows?.find(p => p.proposal_id === selId) ?? rows?.[0] ?? null;
  const changed = (msg: string) => { setFlash(msg); setSelId(''); load(); onChanged?.(); };

  return (
    <>
      {flash && <DoneNote text={flash} onClose={() => setFlash('')} />}
      {err && <LoadError what={what} err={err} retry={load} />}
      {rows === null && !err && <p className="muted">Loading {what}…</p>}
      {rows !== null && rows.length === 0 && !err && <Empty text={empty} />}
      {rows !== null && rows.length > 0 && sel && (
        <div className="rv-layout mdm-review">
          <div className="card rv-list">
            <div className="rv-list-hdr">
              <svg className="ico"><use href="#i-check-circle" /></svg>
              <h4>{rows.length} {mode === 'approver' ? 'ready for your review' : `proposal${rows.length === 1 ? '' : 's'}`}</h4>
            </div>
            <div className="rv-list-body" role="listbox" aria-label={what}>
              {rows.map(p => (
                <button key={p.proposal_id} role="option" aria-selected={p === sel}
                        className={`rv-row${p === sel ? ' active' : ''}`} onClick={() => setSelId(p.proposal_id)}>
                  <span className="rv-row-main">
                    <span className="rv-row-name">{title(p)}</span>
                    <span className="rv-row-sub">
                      {KIND[p.kind]}
                      {p.master_bk && <span className="mdm-mono">{p.master_bk}</span>}
                      {mode === 'proposer' && <StatusPill s={p.status} />}
                    </span>
                  </span>
                  {mode === 'approver' &&
                    <span className="rv-row-side muted">
                      <span className="rv-row-when">{p.proposed_by} · {fmtWhen(p.proposed_at)}</span>
                    </span>}
                </button>
              ))}
            </div>
          </div>
          <div className="rv-detail-wrap">
            <div className="card card-elev rv-detail">
              <ProposalPanel key={sel.proposal_id} id={sel.proposal_id} mode={mode} onChanged={changed} />
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export function ApprovalsTab({ onChanged }: { onChanged?: () => void }) {
  return <ProposalList view="approvals" mode="approver" what="approvals"
                       empty="Nothing needs your approval." onChanged={onChanged} />;
}

export function MyProposals({ view, refreshKey }: { view: 'mine' | 'attention'; refreshKey?: number }) {
  return <ProposalList view={view} mode="proposer" what="proposals" refreshKey={refreshKey}
                       empty={view === 'mine' ? 'No proposals in this view.' : 'Nothing needs your attention.'} />;
}
