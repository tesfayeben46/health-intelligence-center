/**
 * Alert routing page: admin-managed routing CRUD over
 * ops_governance.alert_routing (gateway /api/alerts/routing), plus the live
 * firing/pending alerts from Prometheus (/api/alerts/active).
 *
 * A routing rule maps an alert SELECTOR (severity + a match_label like
 * `service=gateway` or `kind=dq.test`, matched against an alert's labels) to a
 * delivery CHANNEL + recipient. Edits stamp managed_by='runtime' so the six
 * seeded platform defaults an operator changes survive a cold-boot re-seed.
 */
import React, { useEffect, useState } from 'react';
import * as alerts from '../../api/alerts';
import type { RoutingRule, RoutingRuleIn, Severity, ActiveAlert } from '../../api/alerts';

const SEV_PILL: Record<Severity, string> = { critical: 'alert', warning: 'watch', info: 'info' };
const SEVERITIES: Severity[] = ['critical', 'warning', 'info'];

const EMPTY_FORM: RoutingRuleIn = {
  severity: 'warning', match_label: '', channel: '', recipient: '', note: '', enabled: true,
};

function Alerts() {
  const [rules, setRules] = useState<RoutingRule[] | null>(null);
  const [active, setActive] = useState<ActiveAlert[] | null>(null);
  const [activeErr, setActiveErr] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<RoutingRule | 'new' | null>(null);
  const [form, setForm] = useState<RoutingRuleIn>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);

  async function reload() {
    try { setRules(await alerts.listRouting()); setErr(null); }
    catch (e: any) { setErr(String(e.message || e)); }
  }
  useEffect(() => { reload(); }, []);
  useEffect(() => {
    alerts.listActive()
      .then(r => { setActive(r.alerts); setActiveErr(r.error); })
      .catch(e => setActiveErr(String(e.message || e)));
  }, []);

  function openNew() { setForm(EMPTY_FORM); setEditing('new'); }
  function openEdit(r: RoutingRule) {
    setForm({ severity: r.severity, match_label: r.match_label, channel: r.channel,
              recipient: r.recipient, note: r.note, enabled: r.enabled });
    setEditing(r);
  }
  function close() { setEditing(null); }

  async function save() {
    if (!form.match_label.trim() || !form.channel.trim() || !form.recipient.trim()) {
      setErr('Selector, channel and recipient are required.'); return;
    }
    setBusy(true);
    try {
      if (editing === 'new') await alerts.createRouting(form);
      else if (editing) await alerts.updateRouting(editing.id, form);
      close(); await reload();
    } catch (e: any) { setErr(String(e.message || e)); }
    finally { setBusy(false); }
  }

  async function toggle(r: RoutingRule) {
    try { await alerts.updateRouting(r.id, { enabled: !r.enabled }); await reload(); }
    catch (e: any) { setErr(String(e.message || e)); }
  }
  async function remove(r: RoutingRule) {
    if (!confirm(`Delete routing rule for ${r.severity} · ${r.match_label}?`)) return;
    try { await alerts.deleteRouting(r.id); await reload(); }
    catch (e: any) { setErr(String(e.message || e)); }
  }

  return (
    <section>
      <p className="page-lede">Live alerts and where they route · which severity goes to which channel · on-call recipients</p>
      {err && <div className="card card-elev" style={{ borderColor: 'var(--alert, #c0392b)', marginBottom: '1rem' }}>
        <span className="pill alert">error</span> {err}</div>}

      {/* Active alerts (Prometheus) */}
      <div className="card card-elev" style={{ marginBottom: '1.25rem' }}>
        <div className="card-hdr"><h4>Active alerts</h4>
          <span className="meta">firing &amp; pending · from Prometheus</span></div>
        {activeErr && <p className="muted">Alert source unavailable: {activeErr}</p>}
        {active && active.length === 0 && !activeErr &&
          <p className="muted">No alerts firing or pending. All clear.</p>}
        {active && active.length > 0 && (
          <table className="dash">
            <thead><tr><th>Alert</th><th>Severity</th><th>Service</th><th>State</th><th>Since</th><th>Summary</th></tr></thead>
            <tbody>
              {active.map((a, i) => (
                <tr key={i}>
                  <td>{a.name}</td>
                  <td><span className={`pill ${SEV_PILL[(a.severity as Severity)] || 'neutral'}`}>{a.severity}</span></td>
                  <td>{a.service || 'n/a'}</td>
                  <td>{a.state || 'n/a'}</td>
                  <td>{a.active_since ? new Date(a.active_since).toLocaleString() : 'n/a'}</td>
                  <td>{a.summary || 'n/a'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {!active && !activeErr && <p className="muted">Loading…</p>}
      </div>

      {/* Routing rules (CRUD) */}
      <div className="card card-elev">
        <div className="card-hdr">
          <h4>Routing rules</h4>
          <button className="btn btn-sm btn-primary" onClick={openNew}>+ Add rule</button>
        </div>
        {!rules && <p className="muted">Loading…</p>}
        {rules && rules.length === 0 && <p className="muted">No routing rules configured.</p>}
        {rules && rules.length > 0 && (
          <table className="dash">
            <thead><tr>
              <th>Severity</th><th>Selector</th><th>Channel</th><th>Recipient</th>
              <th>Note</th><th>Source</th><th>Enabled</th><th></th>
            </tr></thead>
            <tbody>
              {rules.map(r => (
                <tr key={r.id} style={r.enabled ? undefined : { opacity: 0.5 }}>
                  <td><span className={`pill ${SEV_PILL[r.severity]}`}>{r.severity}</span></td>
                  <td><code>{r.match_label}</code></td>
                  <td>{r.channel}</td>
                  <td>{r.recipient}</td>
                  <td>{r.note || 'n/a'}</td>
                  <td><span className={`pill ${r.managed_by === 'seed' ? 'neutral' : 'good'}`}>{r.managed_by}</span></td>
                  <td>
                    <button className="btn btn-sm btn-ghost" onClick={() => toggle(r)}>
                      {r.enabled ? 'on' : 'off'}
                    </button>
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <button className="btn btn-sm btn-ghost" onClick={() => openEdit(r)}>Edit</button>
                    <button className="btn btn-sm btn-danger" onClick={() => remove(r)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Editor modal */}
      {editing && (
        <div className="modal-bg" onClick={close}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-hdr"><h4>{editing === 'new' ? 'New routing rule' : 'Edit routing rule'}</h4></div>
            <div className="modal-body">
              <div className="field-group">
                <label>Severity</label>
                <select className="search-input" value={form.severity}
                        onChange={e => setForm({ ...form, severity: e.target.value as Severity })}>
                  {SEVERITIES.map(s => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
              <div className="field-group">
                <label>Selector (match_label)</label>
                <input className="search-input" placeholder="service=gateway · kind=dq.test"
                       value={form.match_label} onChange={e => setForm({ ...form, match_label: e.target.value })} />
              </div>
              <div className="field-group">
                <label>Channel</label>
                <input className="search-input" placeholder="pagerduty:nhdw-oncall · slack:#nhdw-platform · email:…"
                       value={form.channel} onChange={e => setForm({ ...form, channel: e.target.value })} />
              </div>
              <div className="field-group">
                <label>Recipient</label>
                <input className="search-input" placeholder="ops-oncall@moh.gov.et · @platform"
                       value={form.recipient} onChange={e => setForm({ ...form, recipient: e.target.value })} />
              </div>
              <div className="field-group">
                <label>Note</label>
                <input className="search-input" placeholder="What condition this covers"
                       value={form.note} onChange={e => setForm({ ...form, note: e.target.value })} />
              </div>
              <div className="field-group">
                <label><input type="checkbox" checked={form.enabled}
                       onChange={e => setForm({ ...form, enabled: e.target.checked })} /> Enabled</label>
              </div>
            </div>
            <div className="modal-foot">
              <button className="btn btn-ghost" onClick={close} disabled={busy}>Cancel</button>
              <button className="btn btn-primary" onClick={save} disabled={busy}>
                {busy ? 'Saving…' : editing === 'new' ? 'Create rule' : 'Save changes'}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

/* widget-module registration (admin catalog runtime)
   1:1; page admin/alerts has exactly this widget. */
import type { AdminWidget } from '../../runtime/types';
const widget: AdminWidget = {
  id: 'adm-alerts-routing',
  Component: Alerts,
};
export default widget;
