/**
 * Review matches: the matcher's suggestions, one at a time.
 *
 * Left: the suggestions still waiting for a steward (GET /api/mdm/review-queue:
 * pending, not under an open proposal, not dismissed; "not sure" stays, flagged).
 * Right: the selected suggestion with its source records side by side
 * (GET /api/mdm/compare). A verdict is a PROPOSAL (POST candidate/{id}/propose):
 * it is checked at once and waits for a different person to approve it in
 * Approvals; nothing reaches the decision log until then. "Not sure" and
 * "Dismiss" record the steward's call without proposing anything.
 * J / K move through the queue when focus is not in a form field.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import * as mdm from '../../api/mdm';
import Comparison from './Comparison';
import { ActionError, DoneNote, LoadError, RULE_LABEL, featureLine, rulePill } from './parts';

type Entity = 'facility' | 'commodity';

function candidateName(c: mdm.QueueCandidate): string {
  const f = c.match_features as Record<string, unknown>;
  return String(f.source_name ?? f.norm_name ?? `${c.left_source_system_code} record`);
}

export default function ReviewTab({ onProposed, onOpenMaster }:
    { onProposed: () => void; onOpenMaster: (bk: string) => void }) {
  const [entity, setEntity] = useState<Entity>('facility');
  const [rule, setRule] = useState('');
  const [queue, setQueue] = useState<mdm.ReviewQueue | null>(null);
  const [err, setErr] = useState('');
  const [selId, setSelId] = useState('');
  const [cmp, setCmp] = useState<mdm.Comparison | null>(null);
  const [cmpErr, setCmpErr] = useState('');
  const [verdict, setVerdict] = useState<'same' | 'different' | null>(null);
  const [bulkAsk, setBulkAsk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actErr, setActErr] = useState('');
  const [flash, setFlash] = useState('');

  const load = useCallback(() => {
    mdm.reviewQueue(entity)
      .then(r => { setQueue(r); setErr(''); })
      .catch(e => setErr(String(e?.message || e)));
  }, [entity]);
  useEffect(load, [load]);

  const all = queue?.candidates ?? null;
  const rows = useMemo(() => all ? (rule ? all.filter(c => c.match_rule === rule) : all) : null, [all, rule]);
  const byRule = useMemo(() => (all ?? []).reduce<Record<string, number>>(
    (a, c) => ({ ...a, [c.match_rule]: (a[c.match_rule] ?? 0) + 1 }), {}), [all]);
  // high confidence = at or above the auto-link score, with a master, and not an
  // ambiguous name (its 1.00 means the name matches several masters)
  const highIds = useMemo(() => (rows ?? [])
    .filter(c => queue && c.score >= queue.high_confidence_score && c.suggested_master_bk
                 && c.match_rule !== 'name_ambiguous')
    .map(c => c.candidate_id), [rows, queue]);

  const sel = rows?.find(c => c.candidate_id === selId) ?? rows?.[0] ?? null;
  const idx = sel && rows ? rows.indexOf(sel) : -1;

  const loadCompare = useCallback(() => {
    if (!sel || sel.entity_class_code !== 'facility') { setCmp(null); setCmpErr(''); return; }
    let live = true;
    setCmp(null); setCmpErr('');
    mdm.compare(sel.candidate_id)
      .then(r => { if (live) setCmp(r); })
      .catch(e => { if (live) setCmpErr(String(e?.message || e)); });
    return () => { live = false; };
  }, [sel?.candidate_id]);
  useEffect(loadCompare, [loadCompare]);
  useEffect(() => { setVerdict(null); setActErr(''); }, [sel?.candidate_id]);

  const move = useCallback((step: number) => {
    if (!rows || rows.length === 0) return;
    setSelId(rows[Math.min(rows.length - 1, Math.max(0, idx + step))].candidate_id);
  }, [rows, idx]);

  // J / K step through the queue unless the steward is typing.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      if (t && (t.closest('input, textarea, select') || t.isContentEditable)) return;
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key === 'j' || e.key === 'J') move(1);
      if (e.key === 'k' || e.key === 'K') move(-1);
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [move]);

  async function act(run: () => Promise<string>) {
    setBusy(true); setActErr(''); setFlash('');
    try {
      const msg = await run();
      setFlash(msg);
      setVerdict(null); setBulkAsk(false);
      if (rows && idx + 1 < rows.length) setSelId(rows[idx + 1].candidate_id);
      load(); onProposed();
    } catch (e: any) {
      setActErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  const sendProposal = () => act(async () => {
    const r = await mdm.propose(sel!.candidate_id, verdict!);
    return r.status === 'checked' ? 'Sent for approval.'
      : 'Not sent: the check failed. The records changed; review the match again.';
  });
  const markNotSure = () => act(async () => {
    await mdm.reviewCandidate(sel!.candidate_id, 'not_sure');
    return 'Marked not sure. It stays in the queue.';
  });
  const dismiss = () => act(async () => {
    await mdm.reviewCandidate(sel!.candidate_id, 'dismissed');
    return 'Suggestion dismissed.';
  });
  const sendBulk = () => act(async () => {
    const r = await mdm.proposeBulk(highIds);
    const failed = r.requested - r.proposed;
    return failed === 0 ? `Sent ${r.proposed} for approval.`
      : `Sent ${r.proposed} for approval. ${failed} could not be sent.`;
  });

  const noun = entity === 'facility' ? 'facility' : 'commodity';
  const plural = entity === 'facility' ? 'facilities' : 'commodities';
  return (
    <>
      <div className="local-controls">
        <span className="lc-label">Show</span>
        <select value={entity} aria-label="List"
                onChange={e => { setEntity(e.target.value as Entity); setRule(''); setSelId(''); }}>
          <option value="facility">Facilities</option>
          <option value="commodity">Commodities</option>
        </select>
        <span className="lc-label">Rule</span>
        <select value={rule} aria-label="Match rule" onChange={e => { setRule(e.target.value); setSelId(''); }}>
          <option value="">All rules · {all?.length ?? 0}</option>
          {Object.keys(RULE_LABEL).map(r => (
            <option key={r} value={r}>{RULE_LABEL[r]} · {byRule[r] ?? 0}</option>
          ))}
        </select>
      </div>

      {queue && highIds.length > 0 && (
        <div className="rv-note">
          <svg className="ico ico-sm"><use href="#i-info" /></svg>
          <span className="rv-note-txt">
            {highIds.length} high-confidence match{highIds.length === 1 ? ' is' : 'es are'} at or above
            the auto-link score and ready to send for approval together.
          </span>
          {!bulkAsk
            ? <button className="btn btn-sm" disabled={busy} onClick={() => setBulkAsk(true)}>Send {highIds.length} for approval</button>
            : <span className="rv-actions">
                <button className="btn btn-sm btn-primary" disabled={busy} onClick={sendBulk}>Send {highIds.length}</button>
                <button className="btn btn-sm btn-ghost" disabled={busy} onClick={() => setBulkAsk(false)}>Cancel</button>
              </span>}
        </div>
      )}
      {flash && <DoneNote text={flash} onClose={() => setFlash('')} />}

      {err && <LoadError what="matches" err={err} retry={load} />}
      {rows === null && !err && <p className="muted">Loading matches…</p>}
      {rows !== null && rows.length === 0 && !err &&
        <p className="muted">{rule ? 'No matches for this filter.' : 'No matches to review.'}</p>}

      {rows !== null && rows.length > 0 && sel && (
        <div className="rv-layout mdm-review">
          <div className="card rv-list">
            <div className="rv-list-hdr">
              <svg className="ico"><use href="#i-check-circle" /></svg>
              <h4>{rows.length} possible match{rows.length === 1 ? '' : 'es'}</h4>
              <span className="mdm-kbd" title="Next match">J</span>
              <span className="mdm-kbd" title="Previous match">K</span>
            </div>
            <div className="rv-list-body" role="listbox" aria-label="Possible matches">
              {rows.map(c => (
                <button key={c.candidate_id} role="option" aria-selected={c === sel}
                        className={`rv-row${c === sel ? ' active' : ''}`}
                        onClick={() => setSelId(c.candidate_id)}>
                  <span className="rv-row-main">
                    <span className="rv-row-name">{candidateName(c)}</span>
                    <span className="rv-row-sub">
                      <span className="mdm-mono">{c.suggested_master_bk ?? c.left_source_system_code}</span>
                      <span className={`pill ${rulePill(c.match_rule)}`}>{RULE_LABEL[c.match_rule] ?? c.match_rule}</span>
                      {c.score.toFixed(2)}
                      {c.review?.outcome === 'not_sure' && <span className="pill neutral">Not sure</span>}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div className="rv-detail-wrap">
            <div className="card card-elev rv-detail">
              <div className="card-hdr">
                <span className="meta">Match {idx + 1} of {rows.length}</span>
                <span className="rv-actions">
                  <button className="btn btn-sm btn-ghost" title="Previous match (K)" disabled={idx <= 0}
                          onClick={() => move(-1)}>Previous</button>
                  <button className="btn btn-sm btn-ghost" title="Next match (J)" disabled={idx >= rows.length - 1}
                          onClick={() => move(1)}>Next</button>
                </span>
              </div>
              <div className="mdm-q">
                <h3 className="rv-detail-name mdm-name">Are these the same {noun}?</h3>
                <span className={`pill ${rulePill(sel.match_rule)}`}>{RULE_LABEL[sel.match_rule] ?? sel.match_rule}</span>
              </div>
              {sel.suggested_master_bk && (
                <div className="mdm-q">
                  <span className="rv-detail-desc">Suggested master <span className="mdm-mono">{sel.suggested_master_bk}</span></span>
                  {entity === 'facility' &&
                    <button className="btn btn-sm btn-ghost" onClick={() => onOpenMaster(sel.suggested_master_bk!)}>Open record</button>}
                </div>
              )}

              {entity === 'facility' && cmpErr && <LoadError what="this comparison" err={cmpErr} retry={loadCompare} />}
              {entity === 'facility' && !cmp && !cmpErr && <p className="muted">Loading comparison…</p>}
              {entity === 'facility' && cmp && <Comparison cmp={cmp} />}
              {entity === 'commodity' && <p className="rv-detail-desc">{featureLine(sel.match_features)}</p>}

              <div className="rv-section-h mdm-section">What do you think?</div>
              {!verdict && (
                <div className="rv-actions">
                  <button className="btn" disabled={busy || !sel.suggested_master_bk}
                          onClick={() => setVerdict('same')}>Same {noun}</button>
                  <button className="btn" disabled={busy} onClick={() => setVerdict('different')}>Different {plural}</button>
                  <button className="btn" disabled={busy || sel.review?.outcome === 'not_sure'} onClick={markNotSure}>Not sure</button>
                  <button className="btn btn-ghost" disabled={busy} onClick={dismiss}>Dismiss this suggestion</button>
                </div>
              )}
              {verdict && (
                <div className="rv-form" role="region" aria-label="Confirm proposal">
                  <p className="rv-detail-desc">
                    {verdict === 'same'
                      ? <>Same {noun}: joins <span className="mdm-mono">{sel.suggested_master_bk}</span> after a second person approves.</>
                      : <>Different {plural}: stays separate after a second person approves.</>}
                  </p>
                  <div className="rv-actions">
                    <button className="btn btn-primary" disabled={busy} onClick={sendProposal}>Send for approval</button>
                    <button className="btn btn-ghost" disabled={busy} onClick={() => setVerdict(null)}>Cancel</button>
                  </div>
                </div>
              )}
              {actErr && <ActionError text="Could not save. Review the match and try again." err={actErr} />}

              <details className="mdm-disclosure">
                <summary>About this suggestion</summary>
                <dl className="mdm-kv">
                  <dt>Score</dt><dd className="mdm-mono">{sel.score.toFixed(2)}</dd>
                  <dt>Rule</dt><dd>{RULE_LABEL[sel.match_rule] ?? sel.match_rule}</dd>
                  <dt>Auto-link score</dt><dd className="mdm-mono">{queue?.high_confidence_score.toFixed(2)}</dd>
                  <dt>Evidence</dt><dd>{featureLine(sel.match_features) || 'n/a'}</dd>
                  {sel.review && <><dt>Marked</dt><dd>Not sure · {sel.review.by}</dd></>}
                </dl>
              </details>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
