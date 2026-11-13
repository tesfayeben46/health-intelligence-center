/**
 * Users and Access (page id admin/users-access, one catalog widget:
 * adm-users-access): access requests and users with their roles.
 *
 * Access requests: via adminApp (/gw/admin/access-requests):
 * pending queue with the review/provision modal (ReviewModal), resolved
 * history. Preserves the ar-* data-testids the provisioning e2e test drives.
 *
 * Users: rows from the perm-admin backend: /gw/perm-admin/roles +
 * /gw/perm-admin/roles/{id} aggregated per user (there is no flat user-list
 * endpoint; the per-role assignments are the source of truth). Role
 * assignment UI: revoke a role chip, assign from the role picker; both hit
 * /gw/perm-admin/user-roles and every change writes an audit row.
 *
 * Table standard: pending/resolved/users tables all use tableKit
 * (fixed height + pagination + search where warranted).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAdminApp } from '../../useAdminApp';
import ReviewModal from './ReviewModal';
import * as api from '../../api/permAdmin';
import { useTableKit, TableToolbar, TablePager, SortTh } from '../../kit/tableKit';

function dashboardPillCls(d: string): string {
  if (d.includes('Infra')) return 'alert';
  if (d.includes('Workshop') || d.includes('Research')) return 'watch';
  return 'info';
}

/* real users, aggregated from per-role assignments */

type RealUser = {
  kc_sub: string;
  username: string;
  email: string | null;
  full_name: string | null;
  realm: string;
  source: string;
  is_active: boolean;
  roles: Array<{ id: number; code: string }>;
};

function initialsOf(u: RealUser): string {
  const src = u.full_name || u.username || '?';
  return src.trim().split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase() || '??';
}

function UsersPanel() {
  const [roles, setRoles] = useState<api.RoleListItem[] | null>(null);
  const [users, setUsers] = useState<RealUser[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [denied, setDenied] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);   // kc_sub being edited

  const reload = useCallback(async () => {
    setErr(null); setDenied(false);
    try {
      const roleList = await api.listRoles();
      setRoles(roleList);
      // No flat user-list endpoint exists: fold the per-role user
      // assignments (the authoritative ops_perm rows) into one user table.
      const details = await Promise.all(roleList.map(r => api.getRole(r.id).catch(() => null)));
      const byUser = new Map<string, RealUser>();
      details.forEach(d => {
        if (!d) return;
        d.users.forEach(u => {
          const row = byUser.get(u.kc_sub) ?? {
            kc_sub: u.kc_sub, username: u.username, email: u.email,
            full_name: u.full_name, realm: u.realm, source: u.source,
            is_active: u.is_active, roles: [],
          };
          if (u.is_active) row.is_active = true;
          if (!row.roles.some(r => r.id === d.id)) row.roles.push({ id: d.id, code: d.code });
          byUser.set(u.kc_sub, row);
        });
      });
      setUsers([...byUser.values()].sort((a, b) =>
        (a.full_name || a.username).localeCompare(b.full_name || b.username)));
    } catch (e: any) {
      const msg = e?.message || String(e);
      if (/^40[13]\b/.test(msg)) setDenied(true);
      else setErr(msg);
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  async function revoke(u: RealUser, roleId: number, code: string) {
    if (!window.confirm(`Revoke ${code} from ${u.full_name || u.username}?`)) return;
    setBusy(u.kc_sub);
    try { await api.revokeUserRole(u.kc_sub, roleId); await reload(); }
    catch (e: any) { setErr(e?.message || String(e)); }
    finally { setBusy(null); }
  }

  async function assign(u: RealUser, roleId: number) {
    setBusy(u.kc_sub);
    try { await api.grantUserRole(u.kc_sub, roleId); await reload(); }
    catch (e: any) { setErr(e?.message || String(e)); }
    finally { setBusy(null); }
  }

  // per-user data scope (ops_identity.user_scope); inline editor row.
  const SCOPE_DIMS = ['geography', 'programme', 'facility', 'artifact_tier', 'workshop_group'];
  const [scopeEdit, setScopeEdit] = useState<{ kc_sub: string; scope: Record<string, string[]> } | null>(null);
  async function openScope(u: RealUser) {
    setBusy(u.kc_sub);
    try { const r = await api.getUserScope(u.kc_sub); setScopeEdit({ kc_sub: u.kc_sub, scope: r.scope }); }
    catch (e: any) { setErr(e?.message || String(e)); }
    finally { setBusy(null); }
  }
  async function saveScope() {
    if (!scopeEdit) return;
    setBusy(scopeEdit.kc_sub);
    try { await api.replaceUserScope(scopeEdit.kc_sub, scopeEdit.scope); setScopeEdit(null); }
    catch (e: any) { setErr(e?.message || String(e)); }
    finally { setBusy(null); }
  }

  const kit = useTableKit(users, {
    pageSize: 10,
    search: u => `${u.full_name ?? ''} ${u.username} ${u.email ?? ''} ${u.realm} ${u.roles.map(r => r.code).join(' ')}`,
    sortVals: {
      user:  u => u.full_name || u.username,
      realm: u => u.realm,
      roles: u => u.roles.length,
      status: u => (u.is_active ? 0 : 1),
    },
    initialSort: { key: 'user', dir: 'asc' },
  });

  if (denied) {
    return (
      <div className="card card-elev">
        <p className="text-sm muted" style={{ margin: '0.4rem 0' }}>
          <svg className="ico ico-sm"><use href="#i-lock" /></svg>{' '}
          The gateway declined the user directory for your role. Browsing and
          assigning roles requires the <code>manage_users</code> capability.
        </p>
      </div>
    );
  }

  return (
    <div className="card card-elev" style={{ paddingTop: '0.8rem' }}>
      {err && (
        <div className="alert-banner mb-sm" style={{ background: 'var(--alert-50)', color: 'var(--alert)' }}>
          {err}
          <button className="btn btn-sm btn-ghost" onClick={() => setErr(null)} style={{ marginLeft: 'auto' }}>Dismiss</button>
        </div>
      )}
      <TableToolbar kit={kit} placeholder="Search name, email, realm, role…" unit="users" />
      <div className="tk-scroll">
        <table className="dash">
          <thead>
            <tr>
              <SortTh kit={kit} k="user">User</SortTh>
              <SortTh kit={kit} k="realm">Realm · source</SortTh>
              <SortTh kit={kit} k="roles">Roles</SortTh>
              <SortTh kit={kit} k="status">Status</SortTh>
              <th>Data scope</th>
              <th>Assign role</th>
            </tr>
          </thead>
          <tbody>
            {kit.pageRows.map(u => (
              <tr key={u.kc_sub}>
                <td>
                  <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                    <span style={{ width: 24, height: 24, borderRadius: '50%', background: 'var(--navy-700)', color: '#fff', display: 'grid', placeItems: 'center', fontSize: '0.66rem', fontWeight: 700, flexShrink: 0 }}>
                      {initialsOf(u)}
                    </span>
                    <div>
                      <strong>{u.full_name || u.username}</strong><br />
                      <span className="text-xs muted">{u.email || u.username}</span>
                    </div>
                  </div>
                </td>
                <td><code>{u.realm}</code><br /><span className="text-xs muted">{u.source}</span></td>
                <td>
                  <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: '0.3rem' }}>
                    {u.roles.map(r => (
                      <button key={r.id}
                              className="pill info"
                              style={{ cursor: 'pointer', border: 'none' }}
                              title={`Revoke ${r.code}`}
                              disabled={busy === u.kc_sub}
                              onClick={() => revoke(u, r.id, r.code)}>
                        {r.code} ×
                      </button>
                    ))}
                    {u.roles.length === 0 && <span className="muted text-xs">no roles</span>}
                  </span>
                </td>
                <td>
                  <span className={`pill ${u.is_active ? 'good' : 'neutral'}`}>{u.is_active ? 'active' : 'revoked'}</span>
                </td>
                <td>
                  <button className="btn btn-sm btn-ghost" disabled={busy === u.kc_sub}
                          data-testid={`scope-btn-${u.username}`}
                          title="Edit this user's data scope (row-level narrowing)"
                          onClick={() => openScope(u)}>Scope…</button>
                </td>
                <td>
                  <select
                    value=""
                    disabled={busy === u.kc_sub}
                    onChange={e => { const id = Number(e.target.value); if (id) assign(u, id); }}
                    aria-label={`Assign a role to ${u.full_name || u.username}`}
                  >
                    <option value="">＋ role…</option>
                    {(roles ?? [])
                      .filter(r => !u.roles.some(ur => ur.id === r.id))
                      .map(r => <option key={r.id} value={r.id}>{r.code} · {r.name}</option>)}
                  </select>
                </td>
              </tr>
            ))}
            {scopeEdit && (
              <tr data-testid="user-scope-editor">
                <td colSpan={6} style={{ background: 'var(--paper-2, #f7f9fa)' }}>
                  <strong className="text-sm">Data scope</strong>
                  <span className="text-xs muted">: per-user row-level narrowing
                    (ops_identity.user_scope, intersected with the role scope; empty = unrestricted).</span>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: '0.5rem', margin: '0.5rem 0' }}>
                    {SCOPE_DIMS.map(dim => (
                      <label key={dim} className="text-xs">
                        <code>{dim}</code>
                        <input style={{ width: '100%' }} data-testid={`uscope-${dim}`}
                               placeholder="unrestricted"
                               value={(scopeEdit.scope[dim] ?? []).join(', ')}
                               onChange={e => {
                                 const vals = e.target.value.split(',').map(x => x.trim()).filter(Boolean);
                                 setScopeEdit(se => {
                                   if (!se) return se;
                                   const scope = { ...se.scope };
                                   if (vals.length) scope[dim] = vals; else delete scope[dim];
                                   return { ...se, scope };
                                 });
                               }} />
                      </label>
                    ))}
                  </div>
                  <div className="flex-row gap-sm">
                    <button className="btn btn-sm" data-testid="uscope-save"
                            disabled={busy != null} onClick={() => void saveScope()}>Save scope</button>
                    <button className="btn btn-sm btn-ghost" onClick={() => setScopeEdit(null)}>Cancel</button>
                  </div>
                </td>
              </tr>
            )}
            {users && users.length === 0 && (
              <tr><td colSpan={6} className="muted" style={{ padding: '1rem', textAlign: 'center' }}>
                No role assignments recorded yet. Users appear here once a role is granted.
              </td></tr>
            )}
            {!users && (
              <tr><td colSpan={6} className="muted" style={{ padding: '1rem' }}>Loading users from role assignments…</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <TablePager kit={kit} />
      <p className="text-xs muted" style={{ marginTop: '0.5rem' }}>
        Rows are the live <code>ops_perm</code> user-role assignments (there is no separate user
        directory). Revoking a user's last role removes them from this view, not from Keycloak.
        Deeper role editing lives on <strong>Roles and Permissions</strong>.
      </p>
    </div>
  );
}

/* the merged page */

function UsersAccess() {
  const { state, app } = useAdminApp();
  const requests: any[] = app.requests || [];
  const resolved: any[] = app.resolved || [];
  const approvedCount = resolved.filter((x: any) => x.decision === 'approved').length;
  const declinedCount = resolved.filter((x: any) => x.decision === 'declined').length;

  const pendingKit = useTableKit(requests, { pageSize: 8 });
  const resolvedKit = useTableKit(resolved, {
    pageSize: 8,
    search: (r: any) => `${r.code} ${r.name} ${r.decision} ${r.note ?? ''}`,
  });

  return (
    <>
    <section id="users-access">
      <p className="page-lede">
        One place for people: the access-request queue from the public dashboard's{' '}
        <em>Request access</em> form (2-business-day SLA) and the live user directory
        with role assignment.
      </p>

      {state.newRequestId && (
        <div className="alert-banner mb-md" style={{ background: 'linear-gradient(135deg, var(--watch-50), #fffbeb)', color: '#6b4a16' }}>
          <span className="ico"><svg className="ico"><use href="#i-bell" /></svg></span>
          <div>
            New access request <strong>{state.newRequestId}</strong> arrived from the public dashboard
            ({' '}<a href="/" style={{ color: 'var(--navy-700)' }}>01-public.html <svg className="ico"><use href="#i-arrow-right" /></svg> Request access</a>) ·
            highlighted in the queue below.
          </div>
        </div>
      )}

      <div className="grid grid-4">
        <div className="ops-kpi warn"><div className="lbl">Pending</div><div className="v">{state.pendingRequestCount}</div><div className="sub">awaiting review</div></div>
        <div className="ops-kpi good"><div className="lbl">Approved</div><div className="v">{approvedCount}</div><div className="sub">provisioned accounts</div></div>
        <div className="ops-kpi alert"><div className="lbl">Declined</div><div className="v">{declinedCount}</div><div className="sub">not granted</div></div>
        <div className="ops-kpi info"><div className="lbl">Resolved</div><div className="v">{resolved.length}</div><div className="sub">decisions on record</div></div>
      </div>
      {app.accessError && (
        <p className="text-sm" style={{ color: 'var(--alert, #d9534f)', marginTop: '0.5rem' }}>
          Could not load requests: {app.accessError}
        </p>
      )}

      {/* Users & role assignment */}
      <div className="section-hdr">
        <h3>Users</h3>
        <span className="meta">live from ops_perm role assignments · assign / revoke inline</span>
      </div>
      <UsersPanel />

      {/* Access requests */}
      <div className="section-hdr">
        <h3>Pending requests</h3>
        <span className="meta">from the public Request-access form</span>
      </div>

      <div className="card card-elev" style={{ paddingTop: '0.8rem' }}>
        <div className="tk-scroll">
          <table className="dash">
            <thead><tr>
              <th>ID</th><th>Submitted</th><th>Applicant</th><th>Organisation</th>
              <th>Requested dashboards</th><th>Purpose</th><th>SLA</th><th></th>
            </tr></thead>
            <tbody>
              {pendingKit.pageRows.map((r: any) => (
                <tr key={r.id} className={state.newRequestId === r.code ? 'highlight-row' : undefined} data-testid="ar-pending-row">
                  <td><code>{r.code}</code>{r.fayda_id && <><br /><span className="pill good" style={{ fontSize: '0.62rem' }}>Fayda</span></>}</td>
                  <td>{r.submitted}</td>
                  <td><strong>{r.name}</strong><br /><span className="muted text-xs">{r.email}</span></td>
                  <td>{r.org}<br /><span className="muted text-xs">{r.role}</span></td>
                  <td>
                    {r.dashboards.flatMap((d: string, i: number) => [
                      i > 0 ? ' ' : null,
                      <span key={i} className={`pill ${dashboardPillCls(d)}`}>{d}</span>,
                    ])}
                  </td>
                  <td className="text-xs muted">{r.purpose}</td>
                  <td><span className={`pill ${r.slaClass}`}>{r.sla}</span></td>
                  <td>
                    <button className="btn btn-sm btn-primary" data-testid="ar-review-btn" onClick={() => app.openReview?.(r.id)}>
                      Review
                    </button>
                  </td>
                </tr>
              ))}
              {requests.length === 0 && (
                <tr><td colSpan={8} className="muted" style={{ padding: '1rem 1.2rem', textAlign: 'center' }}>No pending requests · queue empty.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <TablePager kit={pendingKit} />
      </div>

      <div className="section-hdr"><h3>Recently resolved</h3><span className="meta">decision history</span></div>
      <div className="card card-elev" style={{ paddingTop: '0.8rem' }}>
        <TableToolbar kit={resolvedKit} placeholder="Search id, applicant, decision…" unit="decisions" />
        <div className="tk-scroll">
          <table className="dash">
            <thead><tr><th>ID</th><th>Resolved</th><th>Applicant</th><th>Decision</th><th>Account</th><th>Notes</th></tr></thead>
            <tbody>
              {resolvedKit.pageRows.map((r: any) => (
                <tr key={r.id} data-testid="ar-resolved-row">
                  <td><code>{r.code}</code></td>
                  <td>{r.resolved}</td>
                  <td>{r.name}</td>
                  <td>
                    <span className={`pill ${r.decision === 'approved' ? 'good' : r.decision === 'declined' ? 'alert' : 'watch'}`}>
                      {r.decision === 'approved' ? 'Approved' : r.decision === 'declined' ? 'Declined' : 'More info'}
                    </span>
                  </td>
                  <td>
                    {r.provisioning_status === 'provisioned'
                      ? <span className="pill good" data-testid="ar-prov-pill">Provisioned</span>
                      : r.decision === 'approved'
                        ? <span className="pill watch">Not provisioned</span>
                        : <span className="muted text-xs">n/a</span>}
                  </td>
                  <td className="text-xs muted">{r.note}</td>
                </tr>
              ))}
              {resolved.length === 0 && (
                <tr><td colSpan={6} className="muted" style={{ padding: '1rem 1.2rem', textAlign: 'center' }}>No decisions yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <TablePager kit={resolvedKit} />
      </div>
    </section>
    <ReviewModal />
    </>
  );
}

/* widget-module registration (admin catalog runtime)
   One complex surface is one grantable widget: this module is the page's single catalog widget. */
import type { AdminWidget } from '../../runtime/types';
const widget: AdminWidget = {
  id: 'adm-users-access',
  Component: UsersAccess,
};
export default widget;
