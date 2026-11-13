/**
 * ModelControls: operator actions for one model, shown inside the fleet row's
 * expanded detail (below the drift/performance drilldown):
 *
 *   • Retrain: POST /gw/ml/{model}/retrain  (spawn a runner train Job; a new
 *                 MLflow version is registered: promotion stays separate).
 *   • Promote: POST /gw/ml/{model}/promote  (point @champion / @challenger at
 *                 a chosen version; the version list comes from /versions).
 *   • Run on: POST /gw/ml/{model}/predict  (stage typed inputs to predict-adhoc
 *     inputs       runner Job to poll /gw/ml/jobs/{id} to presigned result CSV).
 *
 * Retrain/promote are role-gated at the gateway (_manage_gate: R-OPS-ADM /
 * R-STEW / R-SUPER). Versions / schema / predict / jobs are PRG/DC.CAT, covered
 * by the operator's ML-catalogue read carve-out in r-ops-adm.yaml; without that
 * OPA grant they show a 403 note, never a broken panel. Same pattern as
 * Review.tsx ReviewActions: local busy/err state, typed-error 403 messaging and
 * inline result banners, no global toast. A Job's status comes from
 * GET /jobs/{id}.
 */
import { useEffect, useRef, useState } from 'react';
import * as ml from '../../api/ml';

function actionError(e: unknown, verb: string): string {
  if (e instanceof ml.MlHttpError) {
    if (e.status === 403)
      return `${verb} denied (403): needs an operator role (R-OPS-ADM / R-STEW / R-SUPER) and the ML-catalogue read grant.`;
    if (e.status === 404)
      return `${verb}: not available (404); the gateway route may not be deployed yet.`;
  }
  return (e as { message?: string })?.message || String(e);
}

const TERMINAL = new Set(['succeeded', 'failed']);

/** Poll a gateway-created runner Job to completion; self-cleaning on unmount. */
function useJobPoll() {
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<ml.MlJobStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const ivRef = useRef<number | null>(null);
  const missRef = useRef(0);

  const stop = () => {
    if (ivRef.current != null) { window.clearInterval(ivRef.current); ivRef.current = null; }
  };
  useEffect(() => stop, []);

  const start = (id: string) => {
    stop();
    setJobId(id); setStatus(null); setErr(null); missRef.current = 0;
    const tick = async () => {
      try {
        const s = await ml.getJob(id);
        setStatus(s);
        if (TERMINAL.has(s.status)) stop();
      } catch (e) {
        // A just-created Job can 404 for a beat; tolerate a few, then surface.
        if (e instanceof ml.MlHttpError && e.status === 404 && missRef.current < 5) {
          missRef.current += 1;
        } else {
          setErr(actionError(e, 'Job status')); stop();
        }
      }
    };
    void tick();
    ivRef.current = window.setInterval(() => void tick(), 3000);
  };

  return { jobId, status, err, start };
}

function JobLine({ label, poll }: { label: string; poll: ReturnType<typeof useJobPoll> }) {
  if (!poll.jobId) return null;
  const st = poll.status?.status ?? 'pending';
  const cls = st === 'succeeded' ? 'good' : st === 'failed' ? 'alert' : 'info';
  return (
    <div className="mlh-ctl-job">
      <span className="muted">{label}</span> <code>{poll.jobId}</code>{' '}
      <span className={`pill ${cls}`}>{st}</span>
      {poll.status?.status === 'succeeded' && poll.status.result_url && (
        <a className="btn btn-sm btn-ghost" href={poll.status.result_url}
           target="_blank" rel="noreferrer" style={{ marginLeft: '.4rem' }}>
          Result CSV <svg className="ico ico-sm"><use href="#i-external" /></svg>
        </a>
      )}
      {poll.status?.error && (
        <div className="mlh-note mlh-note-alert" style={{ marginTop: '.4rem' }}>
          <svg className="ico ico-sm"><use href="#i-alert" /></svg>{poll.status.error}
        </div>
      )}
      {poll.err && (
        <div className="mlh-note mlh-note-alert" style={{ marginTop: '.4rem' }}>
          <svg className="ico ico-sm"><use href="#i-alert" /></svg>{poll.err}
        </div>
      )}
    </div>
  );
}

export default function ModelControls({ model }: { model: string }) {
  /* retrain */
  const retrainPoll = useJobPoll();
  const [retrainBusy, setRetrainBusy] = useState(false);
  const [retrainErr, setRetrainErr] = useState<string | null>(null);

  async function doRetrain() {
    setRetrainErr(null); setRetrainBusy(true);
    try {
      const res = await ml.retrain(model);
      retrainPoll.start(res.job_name);
    } catch (e) { setRetrainErr(actionError(e, 'Retrain')); }
    finally { setRetrainBusy(false); }
  }

  /* promote */
  const [versions, setVersions] = useState<ml.ModelVersion[] | null>(null);
  const [verErr, setVerErr] = useState<string | null>(null);
  const [selVersion, setSelVersion] = useState<string>('');
  const [selAlias, setSelAlias] = useState<'champion' | 'challenger'>('challenger');
  const [promoteBusy, setPromoteBusy] = useState(false);
  const [promoteMsg, setPromoteMsg] = useState<string | null>(null);
  const [promoteErr, setPromoteErr] = useState<string | null>(null);

  async function loadVersions() {
    setVerErr(null);
    try {
      const { versions: vs } = await ml.listVersions(model);
      setVersions(vs);
      setSelVersion(prev => prev || (vs[0]?.version ?? ''));
    } catch (e) { setVerErr(actionError(e, 'Versions')); }
  }

  async function doPromote() {
    if (!selVersion) return;
    setPromoteErr(null); setPromoteMsg(null); setPromoteBusy(true);
    try {
      const r = await ml.promote(model, selVersion, selAlias);
      setPromoteMsg(`@${r.alias} now points at version ${r.version}.`);
      await loadVersions();
    } catch (e) { setPromoteErr(actionError(e, 'Promote')); }
    finally { setPromoteBusy(false); }
  }

  /* run on inputs */
  const runPoll = useJobPoll();
  const [schema, setSchema] = useState<ml.MlSchema | null>(null);
  const [schemaErr, setSchemaErr] = useState<string | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [runBusy, setRunBusy] = useState(false);
  const [runErr, setRunErr] = useState<string | null>(null);

  async function loadSchema() {
    setSchemaErr(null);
    try {
      const s = await ml.getSchema(model);
      setSchema(s);
      const init: Record<string, string> = {};
      for (const f of s.inputs) init[f.name] = f.example != null ? String(f.example) : '';
      setForm(init);
    } catch (e) { setSchemaErr(actionError(e, 'Schema')); }
  }

  useEffect(() => {
    void loadVersions();
    void loadSchema();
    // model is the only dependency; loaders are stable enough for this panel.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model]);

  function buildRow(): Record<string, unknown> {
    const row: Record<string, unknown> = {};
    for (const f of schema?.inputs ?? []) {
      let v = (form[f.name] ?? '').trim();
      // Keep a decimal on float columns so the runner doesn't infer int64.
      const t = (f.type || '').toLowerCase();
      if ((t.includes('double') || t.includes('float')) && /^-?\d+$/.test(v)) v = `${v}.0`;
      row[f.name] = v;
    }
    return row;
  }

  async function doRun() {
    setRunErr(null); setRunBusy(true);
    try {
      const sub = await ml.submitPredict(model, { rows: [buildRow()] });
      runPoll.start(sub.job_id);
    } catch (e) { setRunErr(actionError(e, 'Run')); }
    finally { setRunBusy(false); }
  }

  return (
    <div className="mlh-controls">
      <h5 className="mlh-det-h">Operator controls</h5>
      <div className="mlh-ctl-grid">
        {/* Retrain */}
        <div className="mlh-ctl-group">
          <div className="mlh-ctl-h"><svg className="ico ico-sm"><use href="#i-refresh" /></svg> Retrain</div>
          <p className="muted mlh-det-note">
            Spawn a runner train Job from the model's package; a new MLflow
            version is registered. Promotion to @champion stays a separate step.
          </p>
          <button className="btn btn-sm btn-primary" disabled={retrainBusy} onClick={doRetrain}>
            {retrainBusy ? 'Starting…' : 'Retrain now'}
          </button>
          {retrainErr && (
            <div className="mlh-note mlh-note-alert" style={{ marginTop: '.5rem' }}>
              <svg className="ico ico-sm"><use href="#i-alert" /></svg>{retrainErr}
            </div>
          )}
          <JobLine label="train job" poll={retrainPoll} />
        </div>

        {/* Promote */}
        <div className="mlh-ctl-group">
          <div className="mlh-ctl-h"><svg className="ico ico-sm"><use href="#i-flag" /></svg> Promote</div>
          {verErr ? (
            <div className="mlh-note mlh-note-alert">
              <svg className="ico ico-sm"><use href="#i-alert" /></svg>{verErr}
            </div>
          ) : !versions ? (
            <p className="muted mlh-det-note">Loading versions…</p>
          ) : versions.length === 0 ? (
            <p className="muted mlh-det-note">No registered versions yet.</p>
          ) : (
            <div className="mlh-ctl-row">
              <select className="mlh-select" value={selVersion}
                      onChange={e => setSelVersion(e.target.value)}>
                {versions.map(v => (
                  <option key={v.version} value={v.version}>
                    v{v.version}{v.aliases.length ? ` (@${v.aliases.join(', @')})` : ''}
                  </option>
                ))}
              </select>
              <select className="mlh-select" value={selAlias}
                      onChange={e => setSelAlias(e.target.value as 'champion' | 'challenger')}>
                <option value="champion">@champion</option>
                <option value="challenger">@challenger</option>
              </select>
              <button className="btn btn-sm btn-primary" disabled={promoteBusy} onClick={doPromote}>
                {promoteBusy ? 'Promoting…' : 'Set alias'}
              </button>
            </div>
          )}
          {promoteMsg && (
            <div className="mlh-note mlh-note-good" style={{ marginTop: '.5rem' }}>
              <svg className="ico ico-sm"><use href="#i-check" /></svg>{promoteMsg}
            </div>
          )}
          {promoteErr && (
            <div className="mlh-note mlh-note-alert" style={{ marginTop: '.5rem' }}>
              <svg className="ico ico-sm"><use href="#i-alert" /></svg>{promoteErr}
            </div>
          )}
        </div>

        {/* Run on inputs */}
        <div className="mlh-ctl-group mlh-ctl-run">
          <div className="mlh-ctl-h"><svg className="ico ico-sm"><use href="#i-play" /></svg> Run on inputs</div>
          {schemaErr ? (
            <div className="mlh-note mlh-note-alert">
              <svg className="ico ico-sm"><use href="#i-alert" /></svg>{schemaErr}
            </div>
          ) : !schema ? (
            <p className="muted mlh-det-note">Loading input schema…</p>
          ) : !schema.published || schema.inputs.length === 0 ? (
            <p className="muted mlh-det-note">No input schema published for this model.</p>
          ) : (
            <>
              <div className="mlh-run-grid">
                {schema.inputs.map(f => (
                  <label key={f.name} className="mlh-run-field">
                    <span className="mlh-run-lbl" title={f.type || ''}>{f.name}</span>
                    <input className="mlh-input" value={form[f.name] ?? ''}
                           placeholder={f.example != null ? String(f.example) : (f.type || '')}
                           onChange={e => setForm(s => ({ ...s, [f.name]: e.target.value }))} />
                  </label>
                ))}
              </div>
              <button className="btn btn-sm btn-primary" disabled={runBusy} onClick={doRun}
                      style={{ marginTop: '.5rem' }}>
                <svg className="ico ico-sm"><use href="#i-play" /></svg>{' '}
                {runBusy ? 'Submitting…' : 'Run on these inputs'}
              </button>
            </>
          )}
          {runErr && (
            <div className="mlh-note mlh-note-alert" style={{ marginTop: '.5rem' }}>
              <svg className="ico ico-sm"><use href="#i-alert" /></svg>{runErr}
            </div>
          )}
          <JobLine label="run job" poll={runPoll} />
        </div>
      </div>
    </div>
  );
}
