/**
 * Alert-subscription modal: real backend (/gw/alerts/subscriptions).
 *
 * Self-subscription: "notify me when alarm probability on <disease by geography>
 * crosses <threshold>". The recipient is the signed-in user's own email (the
 * evaluator resolves it): not arbitrary addresses. That is the correct, secure
 * model: free-form recipient emails are an abuse vector (subscribe a victim to
 * spam) and need verification; team notification, if wanted, belongs as a
 * Keycloak-group feature, not this form. Opened from the topbar bell or a
 * disease widget's Subscribe button (host.openSubscribe pre-fills disease+geo).
 * CRUD via authFetch; biApp.loadAlertSubs() refreshes the cache that drives the
 * list + the topbar badge.
 */
import { useState } from 'react';
import { useBiApp } from '../useBiApp';
import { REGIONS, DISEASES, scopeLabels } from '../biApp';
import { authFetch } from '../api/useAuth';

export default function AlertModal() {
  const { state, app, setAlertModal } = useBiApp();
  const [disease, setDisease] = useState<string>(app.alertDisease || 'all');
  const [geography, setGeography] = useState<string>(app.alertGeo || 'all');
  const [threshold, setThreshold] = useState<number>(app.alertThresh ?? 0.6);
  const [cadence, setCadence] = useState<string>(app.alertNotify || 'realtime');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string>('');

  async function subscribe() {
    setBusy(true); setMsg('');
    try {
      const r = await authFetch('/gw/alerts/subscriptions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ disease, geography, metric: 'alarm_probability', op: '>=', threshold, cadence }),
      });
      if (!r.ok) { const j = await r.json().catch(() => ({})); setMsg(j.detail || `Could not subscribe (${r.status}).`); }
      else { setMsg('Subscribed. You will be emailed when this alert fires.'); await app.loadAlertSubs(); }
    } catch { setMsg('Network error creating the subscription.'); }
    finally { setBusy(false); }
  }
  async function remove(id: string) {
    await authFetch(`/gw/alerts/subscriptions/${encodeURIComponent(id)}`, { method: 'DELETE' }).catch(() => {});
    await app.loadAlertSubs();
  }
  async function toggle(id: string, status: string) {
    await authFetch(`/gw/alerts/subscriptions/${encodeURIComponent(id)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: status === 'active' ? 'paused' : 'active' }),
    }).catch(() => {});
    await app.loadAlertSubs();
  }

  const subs: any[] = state.alertSubs || [];

  return (
    <div className="modal-bg" onClick={(e) => { if (e.target === e.currentTarget) setAlertModal(false); }}>
      <div className="modal" style={{ maxWidth: '640px' }}>
        <div className="modal-hdr">
          <h2><svg className="ico"><use href="#i-bell" /></svg> Alert subscriptions</h2>
          <p>Get an email when alarm probability crosses your threshold, sent to your own account.</p>
        </div>
        <div className="modal-body">

          <div className="grid grid-2 gap-lg">
            <div className="field-group">
              <label>1 · Disease</label>
              <select value={disease} onChange={(e) => setDisease(e.target.value)}>
                <option value="all">All diseases in scope</option>
                {DISEASES.map((d: any) => <option key={d.key} value={d.key}>{d.name}</option>)}
              </select>
            </div>
            <div className="field-group">
              <label>2 · Geography</label>
              <select value={geography} onChange={(e) => setGeography(e.target.value)}>
                <option value="all">{scopeLabels.allGeography} (national)</option>
                {REGIONS.map((r: { code: string; name: string }) => <option key={r.code} value={r.code}>{r.name}</option>)}
              </select>
            </div>
          </div>

          <div className="grid grid-2 gap-lg">
            <div className="field-group">
              <label>3 · Alert threshold · <strong>{(threshold * 100).toFixed(0) + '%'}</strong> alarm probability</label>
              <input type="range" min="0.10" max="0.95" step="0.05" value={String(threshold)}
                     onChange={(e) => setThreshold(Number(e.target.value))} style={{ width: '100%' }} />
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.72rem', color: 'var(--ink-4)', marginTop: '0.2rem' }}>
                <span>10%</span><span>50%</span><span>95%</span>
              </div>
            </div>
            <div className="field-group">
              <label>4 · Frequency</label>
              <div className="seg" style={{ width: '100%' }}>
                {['realtime', 'daily', 'weekly'].map(c => (
                  <button key={c} className={cadence === c ? 'active' : ''} onClick={() => setCadence(c)} style={{ flex: 1 }}>
                    {c === 'realtime' ? 'Real-time' : c === 'daily' ? 'Daily digest' : 'Weekly'}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <button className="btn btn-primary" onClick={subscribe} disabled={busy}>
              <svg className="ico"><use href="#i-plus" /></svg> {busy ? 'Subscribing…' : 'Subscribe'}
            </button>
            <span className="text-xs muted">Notifications go to your account email.</span>
          </div>
          {msg && <p className="text-sm" style={{ marginTop: '0.5rem', color: msg.startsWith('Subscribed') ? 'var(--good)' : 'var(--alert)' }}>{msg}</p>}

          <div style={{ marginTop: '1.4rem' }}>
            <h4 style={{ fontFamily: 'var(--font-serif)', fontSize: '1rem', marginBottom: '0.6rem' }}>
              Active subscriptions
              <span className="text-xs muted" style={{ fontWeight: 'normal' }}>· <span>{subs.length}</span></span>
            </h4>
            {subs.length > 0 ? (
              <div className="sub-list">
                {subs.map((s: any) => (
                  <div key={s.id} className="sub-row" style={{ opacity: s.status === 'paused' ? 0.55 : 1 }}>
                    <span className="sub-meta">
                      <span>{app.diseaseLabelForSub?.(s.disease)}</span>
                      <span className="sep">·</span>
                      <span>{s.geography === 'all' ? scopeLabels.allGeography : s.geography}</span>
                      <span className="sep">·</span>
                      <span>at least <strong>{((s.threshold ?? 0) * 100).toFixed(0) + '%'}</strong></span>
                      <span className="sep">·</span>
                      <span>{app.notifyLabelForSub?.(s.cadence)}</span>
                      {s.status === 'paused' && <><span className="sep">·</span><span className="text-xs muted">paused</span></>}
                    </span>
                    <span style={{ display: 'flex', gap: '0.3rem' }}>
                      <button className="btn btn-sm btn-ghost" onClick={() => toggle(s.id, s.status)} title={s.status === 'active' ? 'Pause' : 'Resume'}>
                        {s.status === 'active' ? 'Pause' : 'Resume'}
                      </button>
                      <button className="sub-remove" onClick={() => remove(s.id)} title="Unsubscribe">
                        <svg className="ico ico-sm"><use href="#i-x" /></svg>
                      </button>
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted text-sm">No subscriptions yet. Set the scope above and click Subscribe.</p>
            )}
          </div>

        </div>
        <div className="modal-foot">
          <button className="btn btn-ghost" onClick={() => setAlertModal(false)}>Close</button>
        </div>
      </div>
    </div>
  );
}
