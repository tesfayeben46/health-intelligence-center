/**
 * Notification panel: the topbar bell's dropdown. real backend
 * (/gw/alerts/notifications). Lists the caller's FIRED disease-surveillance
 * alerts (written by the evaluator: gateway + alert_evaluation DAG), newest
 * first, unread highlighted. Clicking a row marks it read; "Mark all read"
 * clears the badge. "Manage subscriptions" opens the AlertModal (create/pause/
 * delete the subscriptions that spawn these): so the bell is the single entry
 * point for both fired alerts and their subscriptions.
 *
 * Owner-scoped end to end: the gateway only ever returns the caller's own rows.
 */
import { useBiApp } from '../useBiApp';

function relTime(iso: string | null): string {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function NotificationPanel() {
  const { state, app, setNotifPanel, setAlertModal } = useBiApp();
  const items: any[] = state.alertNotifications || [];
  const unread = state.alertNotifUnread || 0;

  return (
    <div className="modal-bg" style={{ alignItems: 'flex-start', justifyContent: 'flex-end' }}
         onClick={(e) => { if (e.target === e.currentTarget) setNotifPanel(false); }}>
      <div className="modal" style={{ maxWidth: '440px', width: '440px', marginTop: '4.2rem', marginRight: '1rem' }}
           data-testid="notif-panel">
        <div className="modal-hdr" style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
          <div>
            <h2><svg className="ico"><use href="#i-bell" /></svg> Notifications</h2>
            <p>Fired when a forecast crosses one of your alert thresholds.</p>
          </div>
          {unread > 0 && (
            <button className="btn btn-sm btn-ghost" onClick={() => app.markAllNotificationsRead()}
                    data-testid="notif-mark-all">Mark all read</button>
          )}
        </div>
        <div className="modal-body" style={{ maxHeight: '60vh', overflowY: 'auto' }}>
          {items.length > 0 ? (
            <div className="sub-list" data-testid="notif-list">
              {items.map((n: any) => (
                <div key={n.id} className="sub-row" data-testid="notif-row"
                     style={{ alignItems: 'flex-start', cursor: n.read ? 'default' : 'pointer',
                              opacity: n.read ? 0.62 : 1 }}
                     onClick={() => { if (!n.read) app.markNotificationRead(n.id); }}
                     title={n.read ? '' : 'Click to mark read'}>
                  <span style={{ display: 'flex', gap: '0.55rem', alignItems: 'flex-start' }}>
                    <span style={{ marginTop: '0.35rem', width: '8px', height: '8px', borderRadius: '50%',
                                   flex: '0 0 auto',
                                   background: n.read ? 'transparent' : 'var(--alert, #d9534f)',
                                   border: n.read ? '1px solid var(--ink-5, #ccc)' : 'none' }} />
                    <span className="sub-meta" style={{ display: 'flex', flexDirection: 'column', gap: '0.15rem' }}>
                      <span style={{ fontWeight: n.read ? 400 : 600 }}>
                        {n.disease_label || n.disease} · {n.region_name || n.geography}
                      </span>
                      <span className="text-xs muted">{n.message}</span>
                      <span className="text-xs muted" style={{ display: 'flex', gap: '0.4rem' }}>
                        <span>{relTime(n.fired_at)}</span>
                        {n.emailed && <><span className="sep">·</span><span>emailed</span></>}
                      </span>
                    </span>
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted text-sm" data-testid="notif-empty">
              No alerts yet. You’ll be notified here (and by email) when a forecast
              crosses one of your subscription thresholds.
            </p>
          )}
        </div>
        <div className="modal-foot" style={{ display: 'flex', justifyContent: 'space-between' }}>
          <button className="btn btn-ghost" onClick={() => { setNotifPanel(false); setAlertModal(true); }}
                  data-testid="notif-manage">
            <svg className="ico"><use href="#i-bell" /></svg> Manage subscriptions
          </button>
          <button className="btn btn-ghost" onClick={() => setNotifPanel(false)}>Close</button>
        </div>
      </div>
    </div>
  );
}
