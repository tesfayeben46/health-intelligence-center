/**
 * System Console: the bundle operator's control page, in the demo control
 * room's own design language.
 *
 * Two controls, by design:
 *   · Rebuild: full from-scratch spin-up (sources -> ingestors -> pipeline),
 *     optionally restoring the derived mart snapshot instead (fast boot)
 *   · Teardown: cancels a running rebuild, resets to ground state
 *     (optionally wiping source volumes: a factory reset that shows the
 *      bundle can initialise on a fresh machine)
 *
 * Progress is a weighted bar: the SSE stream is matched against the ordered
 * milestones the bootstrap scripts emit, each weighted by its wall-clock time.
 */
import { useEffect, useRef, useState } from 'react';
import {
  fetchConsoleStatus, streamEvents,
  type ConsoleStatus, type ConsoleEvent,
} from '../api/console';

const EV_PREFIX: Record<ConsoleEvent['kind'], string> = {
  start: '▶ ', phase: '── ', banner: '== ', step: '▸ ', ok: '  ok: ',
  fail: '  FAIL: ', warn: '  ! ', line: '    ', done: 'done: ', error: 'ERROR: ',
  cancelled: '■ ',
};

/** Ordered milestones (substring -> cumulative %), weighted by wall-clock time. */
const MILESTONES: Record<string, [string, number][]> = {
  rebuild: [
    ['tools', 2], ['k3d cluster', 6], ['images', 14], ['kustomize apply', 20],
    ['wait for postgres', 24], ['DATA seed', 26],
    ['Climate: CSV API', 28], ['MFR: HAPI', 31], ['EthioEMR: OpenMRS', 35],
    ['HCMISWDM + mBrana', 42], ['DHIS2: the slow one', 48],
    ['REALISTIC seeding', 50], ['minio-init complete', 52], ['all sources reachable', 54],
    ['dhis2-ingest-metadata', 56], ['dhis2-ingest-dvs', 59], ['climate-ingest', 61],
    ['ethioemr-ingest', 64], ['hcmiswdm-ingest', 66], ['mbrana-ingest', 68],
    ['mfr-ingest', 70], ['MFR landing loaded', 72],
    ['ch-raw-views complete', 76], ['dbt-staging complete', 80],
    ['bridge-sync complete', 85], ['dbt-vault complete', 90],
    ['dbt-marts complete', 95], ['component views', 96],
    ['APP/SECURITY seed', 98], ['DONE', 100],
  ],
  teardown: [
    ['deleting k3d cluster', 25], ['Stopping all external source stacks', 65],
    ['DHIS2 down', 75], ['HCMISWDM down', 85], ['Climate down', 95],
  ],
};

export default function SystemConsole() {
  const [status, setStatus] = useState<ConsoleStatus | null>(null);
  const [events, setEvents] = useState<ConsoleEvent[]>([]);
  const [progress, setProgress] = useState(0);
  const [phase, setPhase] = useState<string>('');
  const [wipe, setWipe] = useState(false);
  const [fast, setFast] = useState(false);
  const [busy, setBusy] = useState(false);
  const logRef = useRef<HTMLDivElement | null>(null);
  const detach = useRef<(() => void) | null>(null);
  const milestoneIdx = useRef(0);
  const actionRef = useRef<string>('rebuild');

  const refresh = () =>
    fetchConsoleStatus().then(setStatus).catch(() => setStatus(null));

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    fetchConsoleStatus().then((s) => {
      if (s.runner.running) { actionRef.current = s.runner.action || 'rebuild'; attach(); }
      else if (s.runner.rc === 0 && s.runner.action) setProgress(100);
    }).catch(() => {});
    return () => { clearInterval(t); detach.current?.(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' });
  }, [events]);

  function advance(ev: ConsoleEvent) {
    if (ev.kind === 'phase' || ev.kind === 'step' || ev.kind === 'banner') setPhase(ev.text);
    if (ev.kind === 'done') { setProgress(100); setPhase('complete'); return; }
    if (ev.kind === 'error') { setPhase('failed: see log'); return; }
    if (ev.kind === 'cancelled') { setPhase('cancelled'); return; }
    const ms = MILESTONES[actionRef.current] ?? [];
    for (let i = milestoneIdx.current; i < ms.length; i += 1) {
      if (ev.text.includes(ms[i][0])) {
        milestoneIdx.current = i + 1;
        setProgress((p) => Math.max(p, ms[i][1]));
        break;
      }
    }
  }

  function attach() {
    detach.current?.();
    detach.current = streamEvents(
      (ev) => { setEvents((prev) => [...prev, ev]); advance(ev); },
      () => refresh(),
    );
  }

  async function run(action: 'rebuild' | 'teardown') {
    const msg = action === 'rebuild'
      ? (fast
          ? 'FAST rebuild? Restores the packed mart snapshot and SKIPS the ingestion + dbt pipeline (~minutes). Falls back to the full pipeline if no snapshot is bundled. Uncheck for the realistic path that re-derives everything from source.'
          : 'REALISTIC rebuild from scratch? Runs the real pipeline end to end, sources -> ingestors -> dbt -> marts (~25-40 min; longer after a factory reset).')
      : wipe
        ? 'Teardown + WIPE SOURCE VOLUMES (factory reset)? Everything re-seeds from the bundle on the next rebuild.'
        : 'Tear everything down? A running rebuild will be cancelled; source volumes are kept for a fast rebuild.';
    if (!window.confirm(msg)) return;
    setBusy(true);
    const r = await fetch('/api/console/action', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action,
        wipe_sources: action === 'teardown' ? wipe : false,
        fast: action === 'rebuild' ? fast : false,
      }),
    }).then((x) => x.json()).catch(() => ({ ok: false, message: 'console unreachable' }));
    setBusy(false);
    if (!r.ok) { window.alert(r.message || 'refused'); return; }
    actionRef.current = action;
    milestoneIdx.current = 0;
    setEvents([]); setProgress(0); setPhase('starting…');
    attach(); refresh();
  }

  const running = status?.runner.running ?? false;
  const rc = status?.runner.rc;
  const barClass = running ? 'live' : rc === 0 ? 'ok' : rc == null ? '' : 'bad';

  return (
    <>
      {/* hero: status + progress bar */}
      <div className="card console-hero">
        <div className="console-hero-top">
          <div>
            <h2>System console</h2>
            <p className="muted text-sm">
              One button up, one button down. Rebuild seeds only the source replicas:
              the real ingestion + dbt pipeline produces everything downstream.
            </p>
          </div>
          <div className="console-hero-stat">
            <span className="pct">{Math.round(progress)}<small>%</small></span>
            <span className="muted text-xs">
              {running ? (status?.runner.action || 'running') : rc === 0 ? 'idle · last run ok'
                : rc == null ? 'idle' : 'idle · last run failed'}
            </span>
          </div>
        </div>
        <div className={`console-bar ${barClass}`}>
          <div className="console-bar-fill" style={{ width: `${progress}%` }} />
        </div>
        <div className="console-bar-caption">
          <span className="muted text-xs one-line">{phase || '-'}</span>
          {running && <span className="pulse-dot" />}
        </div>
      </div>

      <div className="console-grid">
        <div className="card">
          <h3>Actions</h3>
          <div className="console-actions">
            <button className="primary" disabled={busy || running} onClick={() => run('rebuild')}>
              ⟳&nbsp; Rebuild system{fast ? ' (fast)' : ''}
            </button>
            <button className="danger" disabled={busy} onClick={() => run('teardown')}>
              ⏻&nbsp; Teardown
            </button>
            <label className="kind-toggle fast-toggle" title="Restore the packed mart snapshot and skip the ingestion + dbt pipeline. Falls back to the realistic path if no snapshot is bundled. The realistic path is the source of truth and re-derives everything.">
              <input type="checkbox" checked={fast} onChange={(e) => setFast(e.target.checked)} />
              <span>fast boot (restore mart snapshot)<br /><small className="muted">skips the pipeline: realistic path re-derives from source</small></span>
            </label>
            <label className="kind-toggle wipe-toggle" title="compose down -v: every source re-seeds from the bundle on the next rebuild; proves a fresh-machine boot">
              <input type="checkbox" checked={wipe} onChange={(e) => setWipe(e.target.checked)} />
              <span>also wipe source volumes<br /><small className="muted">factory reset: fresh-machine test</small></span>
            </label>
          </div>
          <p className="muted text-xs">
            Teardown always works: it cancels a running rebuild and resets to
            ground state so Rebuild can retry from scratch.
          </p>

          <h3>State</h3>
          <div className="console-state-row">
            <span><span className={`status-dot ${status?.cluster_up ? 'on' : 'off'}`} />k3d cluster</span>
            <span className="muted text-xs">
              {status?.cluster_up ? `${status.pods.ready}/${status.pods.total} pods` : 'down'}
            </span>
          </div>
          {Object.entries(status?.sources ?? {}).map(([name, up]) => (
            <div key={name} className="console-state-row">
              <span><span className={`status-dot ${up ? 'on' : 'off'}`} />{name}</span>
            </div>
          ))}

          <h3>Dashboards</h3>
          {Object.entries(status?.dashboards ?? {}).map(([name, url]) => (
            <a key={name} className="console-dash-link" href={url}
               target="_blank" rel="noreferrer">{name} ↗</a>
          ))}
        </div>

        <div className="card console-log-card">
          <h3>Live progress {status?.runner.action ? <span className="muted text-sm">· {status.runner.action}</span> : null}</h3>
          <div className="console-log" ref={logRef}>
            {events.length === 0
              ? <span className="muted">Idle: run an action to see live progress.</span>
              : events.map((ev, i) => (
                  <div key={i} className={`ev ev-${ev.kind}`}>{EV_PREFIX[ev.kind]}{ev.text}</div>
                ))}
          </div>
        </div>
      </div>
    </>
  );
}
