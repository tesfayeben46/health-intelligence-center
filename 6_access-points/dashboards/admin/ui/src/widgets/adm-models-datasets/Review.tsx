/**
 * Project review console (part of page admin/models-datasets, widget
 * adm-models-datasets): the project-centric public-review surface. The unit of
 * review is the project: its owner submits it to become public; a reviewer
 * inspects the essential-files checklist and the file listing, optionally
 * spawns a lab to run its notebooks, then approves (the project becomes
 * public), rejects or requests changes.
 *
 * Master-detail:
 *   Left: GET /gw/graduation/project-queue: projects awaiting review
 *           (review_status in_review | changes_requested), newest first.
 *   Right: GET /gw/graduation/project/{id}: project meta + review_status, the
 *           live essential-files checklist, the file listing (so the reviewer
 *           can inspect), the verdict trail, and the Approve / Reject actions +
 *           Open-in-JupyterLab. A feed that cannot load says so.
 *
 * The routes are reviewer-gated server-side; the nav item is catalog-gated, and
 * this page keeps a defence-in-depth guard: a 403 renders a
 * "reviewer access required" state, never an opaque error dump.
 */
import { useCallback, useEffect, useState, type ReactNode } from 'react';
import {
  listProjectQueue, getProjectDetail, approveProject, rejectProject,
  spawnReviewerLab, GraduationHttpError,
  reviewStatusLabel, reviewStatusPillClass, fmtBytes, fmtDate, fmtDateTime,
  type ProjectQueueItem, type ProjectReviewDetail, type ChecklistItem,
} from '../../api/graduation';
import { useTableKit, TableToolbar, TablePager } from '../../kit/tableKit';
import '../../styles/review.css';

/* small pieces */

function Fact({ icon, label, children }: {
  icon: string; label: string; children: ReactNode;
}) {
  return (
    <div className="rv-fact">
      <svg className="ico"><use href={`#${icon}`} /></svg>
      <div>
        <div className="rv-fact-lbl">{label}</div>
        <div className="rv-fact-val">{children}</div>
      </div>
    </div>
  );
}

/** "essentials present" badge: the one-glance verdict of the checklist. */
function EssentialsBadge({ present }: { present: boolean }) {
  return present
    ? <span className="pill good">essentials complete</span>
    : <span className="pill watch">essentials missing</span>;
}

/* essential-files checklist (each row: requirement, verdict, matches) */

function ChecklistReport({ items }: { items: ChecklistItem[] }) {
  if (!items || items.length === 0) {
    return <p className="rv-hint muted">No checklist has been computed for this project yet.</p>;
  }
  return (
    <div className="rv-table-scroll">
      <table className="dash rv-table">
        <thead>
          <tr><th>Requirement</th><th>Result</th><th>Matched files</th></tr>
        </thead>
        <tbody>
          {items.map((it, i) => {
            const cls = it.present ? 'good' : (it.required ? 'alert' : 'watch');
            const label = it.present ? 'present' : (it.required ? 'missing' : 'optional');
            return (
              <tr key={i}>
                <td>
                  {it.requirement}
                  {!it.required && <span className="muted"> · optional</span>}
                </td>
                <td><span className={`pill ${cls}`}>{label}</span></td>
                <td>
                  {it.matched && it.matched.length > 0
                    ? it.matched.map((m, j) => (
                        <code key={j} style={{ marginRight: '.35rem' }}>{m}</code>
                      ))
                    : <span className="muted">n/a</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* project file listing (so the reviewer can inspect what they publish) */

function FileListing({ files }: { files: ProjectReviewDetail['files'] }) {
  if (!files || files.length === 0) {
    return <p className="rv-hint muted">This project has no files.</p>;
  }
  return (
    <div className="rv-table-scroll">
      <table className="dash rv-table">
        <thead>
          <tr><th>File</th><th>Size</th><th>Last modified</th></tr>
        </thead>
        <tbody>
          {files.map((f, i) => (
            <tr key={i}>
              <td><code>{f.name}</code></td>
              <td>{fmtBytes(f.size)}</td>
              <td>{fmtDateTime(f.last_modified)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* verdict trail (prior review decisions on this project) */

function ReviewTrail({ reviews }: { reviews: ProjectReviewDetail['reviews'] }) {
  if (!reviews || reviews.length === 0) {
    return <p className="rv-hint muted">No prior review decisions on this project.</p>;
  }
  return (
    <div className="rv-table-scroll">
      <table className="dash rv-table">
        <thead>
          <tr><th>Verdict</th><th>Reviewer</th><th>When</th><th>Note</th></tr>
        </thead>
        <tbody>
          {reviews.map((r, i) => (
            <tr key={r.id || i}>
              <td>
                <span className={`pill ${reviewStatusPillClass(r.verdict)}`}>
                  {reviewStatusLabel(r.verdict)}
                </span>
              </td>
              <td>{r.reviewer_username || r.reviewer || 'n/a'}</td>
              <td>{fmtDateTime(r.reviewed_at)}</td>
              <td>{(r.comment || r.note) ? <span className="muted">{r.comment || r.note}</span> : 'n/a'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* approve / reject actions + reviewer lab spawn */

type ActionMode = 'none' | 'approve' | 'reject';

function ReviewActions({ detail, onDone }: {
  detail: ProjectReviewDetail;
  onDone: (message: string) => void;
}) {
  const id = detail.project.id;
  const [mode, setMode] = useState<ActionMode>('none');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Approve form
  const [approveComment, setApproveComment] = useState('');
  // Reject form
  const [rejectNote, setRejectNote] = useState('');
  const [changesRequested, setChangesRequested] = useState(true);

  // Reviewer lab spawn (runs the project's notebooks)
  const [labBusy, setLabBusy] = useState(false);
  const [labMsg, setLabMsg] = useState<string | null>(null);
  const [labOk, setLabOk] = useState(true);

  function actionError(e: unknown, verb: string): string {
    if (e instanceof GraduationHttpError && e.status === 403) {
      return `${verb} denied (403): your role isn't authorised to decide reviews.`;
    }
    return (e as any)?.message || String(e);
  }

  async function doApprove() {
    setBusy(true); setErr(null);
    try {
      const res = await approveProject(id, { comment: approveComment.trim() || undefined });
      const note = res.integration && res.integration.integrated === false && res.integration.reason
        ? ` (model integration: ${res.integration.reason})`
        : '';
      onDone(`Approved "${detail.project.name}". It is now ${res.visibility || 'public'}${note}`);
    } catch (e: any) {
      setErr(actionError(e, 'Approval'));
      setBusy(false);
    }
  }

  async function doReject() {
    setBusy(true); setErr(null);
    try {
      const res = await rejectProject(id, {
        note: rejectNote.trim() || undefined,
        changes_requested: changesRequested,
      });
      onDone(changesRequested
        ? `Changes requested on "${detail.project.name}". The owner can fix and re-submit.`
        : `Rejected "${detail.project.name}".`
        + (res.review_status ? ` (status: ${reviewStatusLabel(res.review_status)})` : ''));
    } catch (e: any) {
      setErr(actionError(e, 'Rejection'));
      setBusy(false);
    }
  }

  async function doSpawnLab() {
    setLabBusy(true); setLabMsg(null); setLabOk(true);
    try {
      const res = await spawnReviewerLab(id, 'Small');
      if (res.url) {
        window.open(res.url, '_blank', 'noreferrer');
        setLabMsg('Lab opening in a new tab. It may take a moment to finish starting.');
      } else {
        setLabMsg(`Lab spawn requested (status: ${res.status || 'spawning'}); reopen once the pod is up.`);
      }
    } catch (e: any) {
      const m = e instanceof GraduationHttpError
        ? `Could not open a lab (HTTP ${e.status}): ${e.message}`
        : `Could not open a lab: ${e?.message || String(e)}`;
      setLabOk(false);
      setLabMsg(m);
    } finally {
      setLabBusy(false);
    }
  }

  return (
    <div className="rv-section">
      <h4 className="rv-section-h">Decision</h4>

      {/* reviewer lab: run the project's notebooks before deciding */}
      <div className="rv-actions" style={{ marginBottom: '.6rem' }}>
        <button className="btn btn-ghost" onClick={doSpawnLab} disabled={labBusy}>
          <svg className="ico"><use href="#i-external" /></svg>
          {labBusy ? 'Opening lab…' : 'Open in JupyterLab'}
        </button>
        <span className="rv-hint muted" style={{ alignSelf: 'center' }}>
          Spawn a lab on this project to run its notebooks before you approve.
        </span>
      </div>
      {labMsg && (
        <div className={`rv-note ${labOk ? 'rv-note-good' : 'rv-note-alert'}`} style={{ marginBottom: '.6rem' }}>
          <svg className="ico ico-sm"><use href={labOk ? '#i-check-circle' : '#i-alert'} /></svg>
          <span className="rv-note-txt">{labMsg}</span>
        </div>
      )}

      {mode === 'none' && (
        <div className="rv-actions">
          <button className="btn btn-primary" onClick={() => { setMode('approve'); setErr(null); }}>
            <svg className="ico"><use href="#i-check-circle" /></svg> Approve &amp; publish
          </button>
          <button className="btn btn-ghost" onClick={() => { setMode('reject'); setErr(null); }}>
            <svg className="ico"><use href="#i-x-circle" /></svg> Reject / request changes
          </button>
        </div>
      )}

      {mode === 'approve' && (
        <div className="rv-form">
          <p className="rv-hint muted">
            Approving makes this project <strong>public</strong>: everyone in the workshop
            can find and open it. Its model is queued for catalogue integration.
          </p>
          <div className="field-group">
            <label>Comment (optional)</label>
            <textarea
              rows={2}
              value={approveComment}
              onChange={e => setApproveComment(e.target.value)}
              placeholder="Reviewer note recorded with the approval"
            />
          </div>

          {err && (
            <div className="rv-note rv-note-alert">
              <svg className="ico ico-sm"><use href="#i-alert" /></svg>
              {err}
            </div>
          )}

          <div className="rv-actions">
            <button className="btn btn-primary" onClick={doApprove} disabled={busy}>
              <svg className="ico"><use href="#i-check" /></svg>
              {busy ? 'Approving…' : 'Confirm approval'}
            </button>
            <button className="btn btn-ghost" onClick={() => { setMode('none'); setErr(null); }} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {mode === 'reject' && (
        <div className="rv-form">
          <p className="rv-hint muted">
            Record a verdict for the owner. <strong>Request changes</strong> lets them fix
            and re-submit; a plain <strong>reject</strong> also allows re-submission but
            reads as a harder no. A note tells them what to change.
          </p>
          <label className="rv-check" style={{ display: 'flex', gap: '.5rem', alignItems: 'center', margin: '0 0 .4rem' }}>
            <input
              type="checkbox"
              checked={changesRequested}
              onChange={e => setChangesRequested(e.target.checked)}
            />
            <span>Request changes (owner can re-submit): untick to reject outright</span>
          </label>
          <div className="field-group">
            <label>Note (optional)</label>
            <textarea
              rows={3}
              value={rejectNote}
              onChange={e => setRejectNote(e.target.value)}
              placeholder="What needs to change before this can be published"
            />
          </div>

          {err && (
            <div className="rv-note rv-note-alert">
              <svg className="ico ico-sm"><use href="#i-alert" /></svg>
              {err}
            </div>
          )}

          <div className="rv-actions">
            <button className="btn btn-danger" onClick={doReject} disabled={busy}>
              <svg className="ico"><use href="#i-x-circle" /></svg>
              {busy ? 'Recording…' : (changesRequested ? 'Request changes' : 'Confirm rejection')}
            </button>
            <button className="btn btn-ghost" onClick={() => { setMode('none'); setErr(null); }} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/* detail panel */

function ReviewDetail({ id, onDone }: {
  id: string;
  onDone: (message: string) => void;
}) {
  const [detail, setDetail] = useState<ProjectReviewDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let stale = false;
    setDetail(null); setErr(null);
    getProjectDetail(id)
      .then(d => { if (!stale) setDetail(d); })
      .catch(e => { if (!stale) setErr(e.message); });
    return () => { stale = true; };
  }, [id]);

  if (err) {
    return (
      <div className="card card-elev rv-detail">
        <div className="rv-note rv-note-alert">
          <svg className="ico ico-sm"><use href="#i-alert" /></svg>
          Failed to load this project: {err}
        </div>
      </div>
    );
  }
  if (!detail) {
    return <div className="card card-elev rv-detail"><p className="muted">Loading project…</p></div>;
  }

  const p = detail.project;

  return (
    <div className="card card-elev rv-detail">
      <div className="rv-detail-badges">
        <span className={`pill ${reviewStatusPillClass(p.review_status)}`}>{reviewStatusLabel(p.review_status)}</span>
        <span className="pill neutral">{p.visibility}</span>
        <EssentialsBadge present={detail.all_essentials_present} />
      </div>
      <h3 className="rv-detail-name">{p.name}</h3>
      {p.description
        ? <p className="rv-detail-desc">{p.description}</p>
        : <p className="rv-hint muted">No description was provided with this project.</p>}

      <div className="rv-facts">
        <Fact icon="i-user" label="Owner">
          {p.owner_username || p.owner_kc_sub}
        </Fact>
        <Fact icon="i-calendar" label="Submitted">
          {fmtDateTime(p.submitted_at)}
        </Fact>
        <Fact icon="i-file" label="Files">
          {detail.file_count} file{detail.file_count === 1 ? '' : 's'}
        </Fact>
      </div>

      {/* essential-files checklist */}
      <div className="rv-section">
        <h4 className="rv-section-h">Essential-files checklist</h4>
        <ChecklistReport items={detail.checklist} />
        {!detail.all_essentials_present && (
          <p className="rv-hint muted">
            Some required essentials are missing. This does not block approval; the
            human reviewer decides, but the owner may want to add them first.
          </p>
        )}
      </div>

      {/* file listing */}
      <div className="rv-section">
        <h4 className="rv-section-h">Files ({detail.file_count})</h4>
        <FileListing files={detail.files} />
      </div>

      {/* verdict trail */}
      <div className="rv-section">
        <h4 className="rv-section-h">Review history</h4>
        <ReviewTrail reviews={detail.reviews} />
      </div>

      <ReviewActions detail={detail} onDone={onDone} />
    </div>
  );
}

/* the console */

export default function Review() {
  const [queue, setQueue] = useState<ProjectQueueItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [denied, setDenied] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const reload = useCallback(() => {
    setQueue(null); setErr(null); setDenied(false);
    listProjectQueue()
      .then(q => setQueue(q))
      .catch(e => {
        if (e instanceof GraduationHttpError && (e.status === 403 || e.status === 401)) {
          setDenied(true);
        } else {
          setErr(e.message);
        }
      });
  }, []);

  useEffect(() => { reload(); }, [reload]);

  // Table standard: the queue list gets search + a fixed-height scroll +
  // pagination.
  const kit = useTableKit(queue, {
    pageSize: 12,
    search: it => `${it.name} ${it.slug} ${it.review_status} ${it.owner_username ?? it.owner_kc_sub}`,
  });

  // Auto-select the first visible project so the detail panel is never blank
  // when there's a queue. Keeps a valid manual selection; re-picks the first if
  // the selection is emptied (e.g. after an approve/reject) or falls out of the
  // current filter/page. No-op when the queue is empty.
  const visible = queue ?? [];
  useEffect(() => {
    const rows = kit.pageRows;
    if (rows.length === 0) return;
    if (!selected || !visible.some(it => it.id === selected)) setSelected(rows[0].id);
  }, [kit.pageRows, visible, selected]);

  // 403: the gateway said this role may not review.
  if (denied) {
    return (
      <section className="rv-page">
        <div className="card card-elev rv-denied">
          <svg className="ico"><use href="#i-shield" /></svg>
          <h4>Reviewer access required</h4>
          <p>The gateway declined the review queue for your role (HTTP 403).
          Only reviewer-capable roles may see and decide project submissions.</p>
        </div>
      </section>
    );
  }

  function onActionDone(message: string) {
    setNotice(message);
    setSelected(null);
    reload();
  }

  const items = queue ?? [];

  return (
    <section className="rv-page">
      <p className="page-lede">
        Projects submitted to become <strong>public</strong>. Inspect each project's
        essential-files checklist and file listing, optionally open a lab to run its
        notebooks, then <strong>approve &amp; publish</strong> it, or <strong>reject /
        request changes</strong> back to the owner with a note.
      </p>

      {notice && (
        <div className="rv-note rv-note-good">
          <svg className="ico ico-sm"><use href="#i-check-circle" /></svg>
          <span className="rv-note-txt">{notice}</span>
          <button className="rv-note-dismiss" onClick={() => setNotice(null)} aria-label="Dismiss">
            <svg className="ico ico-sm"><use href="#i-x" /></svg>
          </button>
        </div>
      )}
      {err && (
        <div className="rv-note rv-note-alert">
          <svg className="ico ico-sm"><use href="#i-alert" /></svg>
          Failed to load the review queue: {err}
        </div>
      )}

      <div className="rv-layout">
        {/* queue list */}
        <div className="card card-elev rv-list">
          <div className="rv-list-hdr">
            <svg className="ico"><use href="#i-graduation" /></svg>
            <h4>
              {queue === null && !err
                ? 'Loading queue…'
                : `${items.length} awaiting review`}
            </h4>
          </div>

          {queue !== null && items.length > 0 && (
            <TableToolbar kit={kit} placeholder="Search project, owner…" unit="projects" />
          )}

          {queue !== null && items.length === 0 && !err && (
            <div className="rv-empty">
              <svg className="ico"><use href="#i-check-circle" /></svg>
              <h4>No projects awaiting review</h4>
              <p>The queue is clear. Projects appear here once an owner submits
              them to become public.</p>
            </div>
          )}

          <div className="rv-list-body">
            {kit.pageRows.map(it => {
              const isActive = selected === it.id;
              return (
                <button
                  key={it.id}
                  className={`rv-row ${isActive ? 'active' : ''}`}
                  onClick={() => { setSelected(it.id); setNotice(null); }}
                >
                  <span className="rv-row-main">
                    <span className="rv-row-name">{it.name}</span>
                    <span className="rv-row-sub">
                      <span className={`pill ${reviewStatusPillClass(it.review_status)}`}>
                        {reviewStatusLabel(it.review_status)}
                      </span>
                      <EssentialsBadge present={it.all_essentials_present} />
                      <span className="muted">{it.owner_username || it.owner_kc_sub}</span>
                    </span>
                  </span>
                  <span className="rv-row-side">
                    <span className="rv-row-when muted">{fmtDate(it.submitted_at)}</span>
                    <svg className="ico ico-sm"><use href="#i-chevron-right" /></svg>
                  </span>
                </button>
              );
            })}
            {items.length > 0 && kit.filteredTotal === 0 && (
              <p className="muted text-sm" style={{ padding: '0.6rem 0.7rem' }}>No projects match the search.</p>
            )}
          </div>
          <TablePager kit={kit} />
        </div>

        {/* detail panel */}
        <div className="rv-detail-wrap">
          {selected
            ? <ReviewDetail key={selected} id={selected} onDone={onActionDone} />
            : queue !== null && items.length === 0 && !err
              ? null
              : (
                <div className="card card-elev rv-detail">
                  <p className="muted">Select a project from the queue to review it.</p>
                </div>
              )}
        </div>
      </div>
    </section>
  );
}
