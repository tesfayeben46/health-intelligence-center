/**
 * Roles and Permissions (page id admin/roles-permissions, one catalog widget:
 * adm-roles-permissions). Sections:
 *   1 Roles & grants: roles master list (left pane) and per-role grants
 *     (right pane); a fuller role editor would replace or extend the right
 *     pane.
 *   2 Audit trail: every permission change.
 *   3 Policy (OPA): live policy tester (POST /gw/policy/evaluate).
 * The read-only widget and filter catalogs live on Dashboard Control, next to
 * the placements they back.
 *
 * Data: /gw/perm-admin/* (catalog, roles, grants, user-roles; every write
 * lands an audit_permission_change row) and /gw/policy/evaluate (live
 * decision). Degraded feeds say so.
 *
 * Table standard: roles + audit use tableKit (fixed height +
 * pagination + search/filter controls).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import * as api from '../../api/permAdmin';
import * as policy from '../../api/policy';
import { useTableKit, TableToolbar, TablePager, SortTh } from '../../kit/tableKit';

/* editor constants & helpers */

// Curated capability vocabulary. The gateway catalog does not expose a
// capability list, so this mirrors the codes actually used across
// bundle/seed-pack/app/security/roles.yaml + the gateway's _require_capability
// checks. The list is curated in the SPA, not backend-driven; a role can
// still hold a capability code not shown here, and it round-trips untouched
// because unknown codes that were not rendered are never deleted.
const CAPABILITY_CATALOG: Array<{ code: string; label: string }> = [
  { code: 'manage_users',             label: 'Manage users & role assignments' },
  { code: 'edit_roles',               label: 'Edit roles & permissions' },
  { code: 'manage_groups',            label: 'Manage workshop groups' },
  { code: 'create_group',             label: 'Create a group' },
  { code: 'manage_identity_sources',  label: 'Manage identity sources (SSO)' },
  { code: 'approve_access_request',   label: 'Approve access requests' },
  { code: 'approve_grad',             label: 'Approve artifact graduation' },
  { code: 'approve_artifact_promotion', label: 'Approve artifact promotion' },
  { code: 'approve_privileged_action', label: 'Approve privileged actions' },
  { code: 'submit_artifact',          label: 'Submit artifacts' },
  { code: 'trigger_pipeline',         label: 'Trigger pipeline runs' },
  { code: 'edit_saved_query',         label: 'Edit saved queries' },
  { code: 'view_audit',               label: 'View audit logs' },
  { code: 'view_ops_console',         label: 'View the ops console' },
  { code: 'act_as_owner',             label: 'Act-as-owner (service)' },
  { code: 'assistant:read',           label: 'Assistant · read' },
  { code: 'assistant:run_sql',        label: 'Assistant · run SQL' },
  { code: 'assistant:interact',       label: 'Assistant · interact' },
];

// Role codes the OPA decision router (7_security/opa/policies/common.rego)
// dispatches to a compiled rego bundle. A role whose code is not in this set
// has no rego policy: common.rego's `default decide := {allow:false}` makes
// mart-backed widgets FAIL CLOSED for it until a platform engineer adds
// 7_security/opa/policies/role_<code>.rego + a dispatch line in common.rego.
const OPA_ENFORCED_ROLE_CODES = new Set<string>([
  'R-SUPER', 'R-DEC', 'R-PD-HIV', 'R-RHB-OROMIA', 'R-CONS-ANA', 'R-CONS-SCI',
  'R-CONS-RES', 'R-CONS-PRT', 'R-STEW', 'R-PROD-CLIN', 'R-PROD-SUBMIT',
  'R-OPS-ENG', 'R-OPS-ADM', 'R-OPS-SEC', 'R-PUB-ANON',
]);

// widget id-prefix to dashboard id. Same convention the Dashboard Control
// widget-catalog panel uses (copied, since widgets share no modules). The
// catalog exposes no widget-to-page placement, so the widget-grant editor groups
// by DASHBOARD and the gateway expands each grant across its pages on save.
const WIDGET_PREFIX_DASHBOARD: Record<string, string> = {
  pub: 'public', ov: 'internal-bi', dz: 'internal-bi', rm: 'internal-bi',
  sv: 'internal-bi', ex: 'internal-bi', st: 'internal-bi', adm: 'admin',
  ws: 'workshop',
};

type WgRow = { dashboard_id: string; widget_id: string; page_id?: string };

// The editor's local, mutable draft of a role. Initialised from RoleDetail;
// PUT back as metadata (updateRole) + grants (replaceGrants) on Save.
type Draft = {
  name: string;
  description: string;
  combine_strategy: 'union' | 'intersection';
  capabilities: string[];
  dashboards: string[];
  widgets: WgRow[];
  // Role-level data-scope caps (ops_perm.role_scope); {dimension: [values]}.
  // A dimension absent here is unrestricted.
  scope: Record<string, string[]>;
};

function draftFromDetail(d: api.RoleDetail): Draft {
  return {
    name: d.name,
    description: d.description ?? '',
    combine_strategy: (d.combine_strategy === 'intersection' ? 'intersection' : 'union'),
    capabilities: [...d.capabilities],
    dashboards: [...d.dashboards],
    widgets: d.widgets.map(w => ({ ...w })),          // preserves server page_id
    scope: Object.fromEntries(Object.entries(d.scope ?? {}).map(([k, v]) => [k, [...v]])),
  };
}

// Canonical string of the metadata axes: drives the "metadata dirty" flag.
function canonMeta(d: Draft): string {
  return JSON.stringify({
    name: d.name.trim(), description: d.description.trim(),
    combine: d.combine_strategy, caps: [...d.capabilities].sort(),
  });
}
// Canonical string of the 4 grant axes: drives the "grants dirty" flag.
// Widgets compare at (dashboard::widget) grain: page_id is preserved on save
// but is not user-editable, so it must not by itself mark the role dirty.
function canonGrants(d: Draft): string {
  return JSON.stringify({
    dashboards: [...d.dashboards].sort(),
    widgets: [...new Set(d.widgets.map(w => `${w.dashboard_id}::${w.widget_id}`))].sort(),
    scope: Object.entries(d.scope).filter(([, v]) => v.length)
      .map(([k, v]) => `${k}=${[...v].sort().join(',')}`).sort(),
  });
}

/* 1 Roles & grants */

function RolesGrants({ catalog }: { catalog: api.Catalog | null }) {
  const [roles, setRoles] = useState<api.RoleListItem[] | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<api.RoleDetail | null>(null);
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [denied, setDenied] = useState(false);

  const reloadList = useCallback(async () => {
    try { setRoles(await api.listRoles()); setDenied(false); }
    catch (e: any) {
      const m = e?.message || String(e);
      if (/^40[13]\b/.test(m)) setDenied(true); else setErr(m);
    }
  }, []);
  const reloadDetail = useCallback(async (id: number) => {
    try { setDetail(await api.getRole(id)); }
    catch (e: any) { setErr(e?.message || String(e)); }
  }, []);

  useEffect(() => { reloadList(); }, [reloadList]);
  useEffect(() => {
    if (selectedId != null) reloadDetail(selectedId);
    else setDetail(null);
  }, [selectedId, reloadDetail]);
  // Never leave the detail pane empty on load: once the roles arrive and
  // nothing is selected (and we're not mid-create), auto-select the first one.
  useEffect(() => {
    if (!creating && selectedId == null && roles && roles.length > 0) {
      setSelectedId(roles[0].id);
    }
  }, [roles, selectedId, creating]);

  function select(id: number) { setCreating(false); setSelectedId(id); }

  const kit = useTableKit(roles, {
    pageSize: 12,
    search: r => `${r.code} ${r.name} ${r.description ?? ''}`,
    sortVals: { code: r => r.code, name: r => r.name, users: r => r.user_count },
    initialSort: { key: 'code', dir: 'asc' },
  });

  return (
    <div className="page-section">
      <div className="ps-hdr">
        <div>
          <h3 className="ps-title"><svg className="ico"><use href="#i-shield-check" /></svg> Roles &amp; grants</h3>
          <p className="ps-sub">
            Edit the 4-axis permission model, live from <code>ops_perm</code>. Every save writes an{' '}
            <code>audit_permission_change</code> row tied to a <code>policy_revision</code> (see the audit trail below).
          </p>
        </div>
        <div className="ps-meta">
          <button className="btn btn-sm btn-primary" data-testid="new-role-btn"
                  onClick={() => { setCreating(true); setSelectedId(null); }}>
            <svg className="ico ico-sm"><use href="#i-plus" /></svg> New role
          </button>
        </div>
      </div>

      {err && (
        <div className="alert-banner mb-sm" style={{ background: 'var(--alert-50)', color: 'var(--alert)' }}>
          {err}
          <button className="btn btn-sm btn-ghost" onClick={() => setErr(null)} style={{ marginLeft: 'auto' }}>Dismiss</button>
        </div>
      )}
      {denied && (
        <div className="alert-banner mb-sm" style={{ background: 'var(--alert-50)', color: 'var(--alert)' }}>
          <svg className="ico ico-sm"><use href="#i-lock" /></svg>{' '}
          The gateway declined the roles directory for your session. This editor needs the{' '}
          <code>manage_users</code> capability (and <code>edit_roles</code> to save).
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '380px 1fr', gap: '1rem', alignItems: 'start' }}>
        {/* roles master list (left pane) */}
        <div className="card card-elev" style={{ paddingTop: '0.6rem' }}>
          <TableToolbar kit={kit} placeholder="Search roles…" unit="roles" />
          <div className="tk-scroll" style={{ maxHeight: '62vh' }}>
            <table className="dash">
              <thead><tr>
                <SortTh kit={kit} k="code">Code</SortTh>
                <SortTh kit={kit} k="name">Name</SortTh>
                <SortTh kit={kit} k="users" className="right">Users</SortTh>
              </tr></thead>
              <tbody>
                {kit.pageRows.map(r => (
                  <tr key={r.id}
                      data-testid={`role-row-${r.code}`}
                      onClick={() => select(r.id)}
                      style={{ cursor: 'pointer', background: selectedId === r.id ? 'var(--navy-50, #eef2f8)' : undefined }}>
                    <td><code>{r.code}</code>{!OPA_ENFORCED_ROLE_CODES.has(r.code) &&
                      <span className="pill watch text-xs" title="no rego policy, not OPA-enforced" style={{ marginLeft: '0.35rem' }}>no-OPA</span>}</td>
                    <td>{r.name}</td>
                    <td style={{ textAlign: 'right' }}>{r.user_count}</td>
                  </tr>
                ))}
                {roles && roles.length === 0 && (
                  <tr><td colSpan={3} className="muted" style={{ padding: '1rem', textAlign: 'center' }}>No roles defined yet.</td></tr>
                )}
                {!roles && !denied && <tr><td colSpan={3} className="muted" style={{ padding: '1rem' }}>Loading…</td></tr>}
              </tbody>
            </table>
          </div>
          <TablePager kit={kit} />
        </div>

        {/* editor pane (right) */}
        <div>
          {creating && (
            <NewRoleForm
              onCancel={() => setCreating(false)}
              onCreated={async id => { setCreating(false); await reloadList(); setSelectedId(id); }}
            />
          )}
          {!creating && !detail && (
            <div className="card card-elev" style={{ padding: '2rem', textAlign: 'center', color: 'var(--ink-3)' }}>
              Select a role on the left to edit its 4-axis permission spec, or create a new one.
            </div>
          )}
          {!creating && detail && (
            <RoleDetailEditor
              key={detail.id}
              catalog={catalog}
              detail={detail}
              onSaved={async () => { await reloadDetail(detail.id); await reloadList(); }}
              onDeleted={async () => { setSelectedId(null); setDetail(null); await reloadList(); }}
            />
          )}
        </div>
      </div>
    </div>
  );
}

/* OPA-enforcement banner */
// Shown whenever the selected/created role's code has no rego dispatch. The
// DB grant model is written either way; this makes the enforcement gap explicit.
function OpaWarningBanner({ code }: { code: string }) {
  if (OPA_ENFORCED_ROLE_CODES.has(code)) return null;
  const rego = `role_${code.toLowerCase().replace(/-/g, '_')}.rego`;
  return (
    <div className="alert-banner mb-sm" data-testid="opa-warning"
         style={{ background: 'var(--watch-50, #fffbeb)', color: '#6b4a16',
                  alignItems: 'flex-start', gap: '0.5rem' }}>
      <svg className="ico" style={{ flexShrink: 0 }}><use href="#i-alert" /></svg>
      <div className="text-sm" style={{ lineHeight: 1.55 }}>
        <strong>Not yet OPA-enforced.</strong> <code>{code}</code> has no rego policy in the
        decision router, so this editor's grants shape the dashboard/widget/filter
        surface but <strong>mart-backed widgets fail closed</strong> for it (OPA returns
        <em> deny · unknown role</em>). A platform engineer must add{' '}
        <code>7_security/opa/policies/{rego}</code> and a dispatch line in{' '}
        <code>common.rego</code> before row/column security applies. Editing an
        <em> existing</em> OPA-backed role is fully safe.
      </div>
    </div>
  );
}

// Multi-select capability pills reused by the metadata editor + new-role form.
function CapabilityPicker({ selected, onToggle, disabled }: {
  selected: string[]; onToggle: (code: string) => void; disabled?: boolean;
}) {
  const extra = selected.filter(c => !CAPABILITY_CATALOG.some(k => k.code === c));
  return (
    <div className="flex-row gap-sm" style={{ flexWrap: 'wrap', marginTop: '0.4rem' }}>
      {CAPABILITY_CATALOG.map(({ code, label }) => {
        const on = selected.includes(code);
        return (
          <button key={code} type="button" title={label}
                  data-testid={`cap-${code}`}
                  className={`pill ${on ? 'info' : 'neutral'}`}
                  style={{ cursor: 'pointer', border: 'none' }}
                  onClick={() => onToggle(code)} disabled={disabled}>
            {code}
          </button>
        );
      })}
      {extra.map(code => (
        <button key={code} type="button" title="capability not in the SPA catalog, preserved as-is"
                className="pill good" style={{ cursor: 'pointer', border: 'none' }}
                onClick={() => onToggle(code)} disabled={disabled}>
          {code}
        </button>
      ))}
    </div>
  );
}

/* metadata + capabilities editor (PUT /roles/{id}) */
function MetaCapEditor({ code, draft, patch, disabled }: {
  code: string;
  draft: Draft;
  patch: (p: Partial<Draft>) => void;
  disabled?: boolean;
}) {
  const toggleCap = (c: string) => patch({
    capabilities: draft.capabilities.includes(c)
      ? draft.capabilities.filter(x => x !== c)
      : [...draft.capabilities, c],
  });
  return (
    <div>
      <div className="grid grid-2 gap-lg" style={{ alignItems: 'start' }}>
        <div className="field-group">
          <label>Role code</label>
          <input value={code} disabled readOnly title="Code is immutable. It keys the OPA rego dispatch." />
        </div>
        <div className="field-group">
          <label>Multi-role combine strategy</label>
          <select value={draft.combine_strategy} disabled={disabled}
                  data-testid="role-combine"
                  onChange={e => patch({ combine_strategy: e.target.value as Draft['combine_strategy'] })}>
            <option value="union">union: widen across the user's roles</option>
            <option value="intersection">intersection: narrow to the common grant</option>
          </select>
        </div>
      </div>
      <div className="field-group">
        <label>Display name</label>
        <input value={draft.name} disabled={disabled} data-testid="role-name"
               onChange={e => patch({ name: e.target.value })} />
      </div>
      <div className="field-group">
        <label>Description</label>
        <textarea rows={2} value={draft.description} disabled={disabled} data-testid="role-desc"
                  onChange={e => patch({ description: e.target.value })} />
      </div>
      <h5 className="mt-md">App capabilities <span className="text-xs muted">({draft.capabilities.length})</span></h5>
      <p className="text-xs muted">App-level powers, orthogonal to data access. Curated list (no capability-catalog endpoint).</p>
      <CapabilityPicker selected={draft.capabilities} onToggle={toggleCap} disabled={disabled} />
    </div>
  );
}

/* Axis A: dashboard access */
function DashboardGrantEditor({ catalog, draft, patch, disabled }: {
  catalog: api.Catalog | null; draft: Draft; patch: (p: Partial<Draft>) => void; disabled?: boolean;
}) {
  const toggle = (d: string) => patch({
    dashboards: draft.dashboards.includes(d)
      ? draft.dashboards.filter(x => x !== d)
      : [...draft.dashboards, d],
  });
  return (
    <div>
      <h5 className="mt-md">Dashboard access <span className="text-xs muted">({draft.dashboards.length})</span></h5>
      <p className="text-xs muted">Which dashboards a user with this role can open (axis A · <code>role_dashboard</code>).</p>
      <div className="flex-row gap-sm" style={{ flexWrap: 'wrap', marginTop: '0.4rem' }}>
        {(catalog?.dashboards ?? []).map(d => {
          const on = draft.dashboards.includes(d.id);
          return (
            <button key={d.id} type="button" data-testid={`dash-toggle-${d.id}`}
                    className={`pill ${on ? 'good' : 'neutral'}`}
                    style={{ cursor: 'pointer', border: 'none' }}
                    onClick={() => toggle(d.id)} disabled={disabled}>
              {d.title}
            </button>
          );
        })}
        {!catalog && <span className="muted text-sm">Loading catalog…</span>}
      </div>
    </div>
  );
}

/* Axis C: widget access, grouped by dashboard */
function WidgetGrantEditor({ catalog, draft, patch, disabled }: {
  catalog: api.Catalog | null; draft: Draft; patch: (p: Partial<Draft>) => void; disabled?: boolean;
}) {
  const [q, setQ] = useState('');

  // widget to dashboard: prefix convention, augmented by whatever the role is
  // already granted (authoritative dashboard_id straight from the server).
  const widgetDash = useMemo(() => {
    const m: Record<string, string> = {};
    (catalog?.widgets ?? []).forEach(w => {
      const d = WIDGET_PREFIX_DASHBOARD[w.id.split('-')[0]];
      if (d) m[w.id] = d;
    });
    draft.widgets.forEach(g => { m[g.widget_id] = g.dashboard_id; });
    return m;
  }, [catalog, draft.widgets]);

  const grantedKey = useMemo(() => {
    const s = new Set<string>();
    draft.widgets.forEach(g => s.add(`${g.dashboard_id}::${g.widget_id}`));
    return s;
  }, [draft.widgets]);

  // group surviving widgets under their dashboard (ordered by catalog.dashboards)
  const groups = useMemo(() => {
    const ql = q.trim().toLowerCase();
    const bucket: Record<string, api.Catalog['widgets']> = {};
    (catalog?.widgets ?? []).forEach(w => {
      if (ql && !`${w.id} ${w.title} ${w.kind}`.toLowerCase().includes(ql)) return;
      (bucket[widgetDash[w.id] || '__ungrouped__'] ||= []).push(w);
    });
    const order = [...(catalog?.dashboards ?? []).map(d => d.id), '__ungrouped__'];
    return order
      .filter(id => bucket[id]?.length)
      .map(id => ({
        id,
        title: catalog?.dashboards.find(d => d.id === id)?.title
          ?? (id === '__ungrouped__' ? 'Unmapped widgets' : id),
        widgets: bucket[id],
      }));
  }, [catalog, widgetDash, q]);

  function toggle(dash: string, wid: string) {
    const key = `${dash}::${wid}`;
    const has = draft.widgets.some(g => `${g.dashboard_id}::${g.widget_id}` === key);
    patch({
      // add without page_id: gateway expands across every page the widget is
      // cataloged on; remove drops all page-scoped rows for that pair.
      widgets: has
        ? draft.widgets.filter(g => !(g.dashboard_id === dash && g.widget_id === wid))
        : [...draft.widgets, { dashboard_id: dash, widget_id: wid }],
    });
  }
  function setAll(dash: string, wids: string[], on: boolean) {
    let widgets = draft.widgets.filter(g => g.dashboard_id !== dash || !wids.includes(g.widget_id));
    if (on) widgets = [...widgets, ...wids.map(widget_id => ({ dashboard_id: dash, widget_id }))];
    patch({ widgets });
  }

  return (
    <div>
      <h5 className="mt-md">Widget grants <span className="text-xs muted">
        ({new Set(draft.widgets.map(w => `${w.dashboard_id}::${w.widget_id}`)).size})
      </span></h5>
      <p className="text-xs muted">
        Axis C · <code>role_widget</code>. Grouped by dashboard: the catalog exposes no
        page/section placement, so a checked widget is granted <strong>across every page of
        that dashboard</strong>; untouched grants keep their existing page scope on save.
      </p>
      <span className="search-row" style={{ maxWidth: 320, margin: '0.4rem 0' }}>
        <span className="search-ico"><svg className="ico ico-sm"><use href="#i-search" /></svg></span>
        <input className="search-input" placeholder="Search widgets…" value={q}
               data-testid="widget-search" onChange={e => setQ(e.target.value)} />
      </span>
      <div style={{ maxHeight: '22rem', overflow: 'auto', border: '1px solid var(--line, #e6e0d6)', borderRadius: 'var(--radius-lg, 10px)' }}>
        {groups.map(g => {
          const ids = g.widgets.map(w => w.id);
          const grantedN = ids.filter(id => grantedKey.has(`${g.id}::${id}`)).length;
          const grantable = g.id !== '__ungrouped__';
          return (
            <div key={g.id} style={{ borderBottom: '1px solid var(--line, #eee)' }}>
              <div className="flex-row" style={{ alignItems: 'center', gap: '0.6rem', padding: '0.5rem 0.75rem', background: 'var(--navy-50, #f3f6fb)', position: 'sticky', top: 0 }}>
                <strong className="text-sm">{g.title}</strong>
                <span className="pill neutral text-xs">{grantedN}/{ids.length}</span>
                <span className="tk-spacer" style={{ flex: 1 }} />
                {grantable && (
                  <>
                    <button type="button" className="btn btn-sm btn-ghost" disabled={disabled}
                            data-testid={`widget-all-${g.id}`} onClick={() => setAll(g.id, ids, true)}>All</button>
                    <button type="button" className="btn btn-sm btn-ghost" disabled={disabled}
                            onClick={() => setAll(g.id, ids, false)}>None</button>
                  </>
                )}
              </div>
              <div style={{ padding: '0.35rem 0.75rem' }}>
                {!grantable && <p className="text-xs muted" style={{ margin: '0.2rem 0' }}>No dashboard mapping, not grantable here.</p>}
                {g.widgets.map(w => {
                  const on = grantedKey.has(`${g.id}::${w.id}`);
                  return (
                    <label key={w.id} className="flex-row" data-testid={`widget-row-${w.id}`}
                           style={{ alignItems: 'center', gap: '0.5rem', padding: '0.15rem 0', cursor: grantable && !disabled ? 'pointer' : 'default' }}>
                      <input type="checkbox" checked={on} disabled={disabled || !grantable}
                             onChange={() => toggle(g.id, w.id)} />
                      <code className="text-xs">{w.id}</code>
                      <span className="text-sm">{w.title}</span>
                      <span className="pill neutral text-xs">{w.kind}</span>
                      {w.requires && <span className="pill info text-xs" title="mart-backed, OPA-gated">mart</span>}
                    </label>
                  );
                })}
              </div>
            </div>
          );
        })}
        {catalog && groups.length === 0 && (
          <p className="muted text-sm" style={{ padding: '1rem' }}>No widgets match “{q}”.</p>
        )}
        {!catalog && <p className="muted text-sm" style={{ padding: '1rem' }}>Loading catalog…</p>}
      </div>
    </div>
  );
}

/* Data scope: role_scope ∩ user_scope drives every filter cap */
const SCOPE_DIMENSIONS = ['geography', 'programme', 'facility', 'artifact_tier', 'workshop_group'];

function ScopeEditor({ draft, patch, disabled }: {
  draft: Draft; patch: (p: Partial<Draft>) => void; disabled?: boolean;
}) {
  const setDim = (dim: string, values: string[]) => {
    const scope = { ...draft.scope };
    if (values.length) scope[dim] = values; else delete scope[dim];
    patch({ scope });
  };
  return (
    <div>
      <h5 className="mt-md">Data scope <span className="text-xs muted">
        ({Object.values(draft.scope).filter(v => v.length).length} restricted dimension{Object.values(draft.scope).filter(v => v.length).length === 1 ? '' : 's'})
      </span></h5>
      <p className="text-xs muted" style={{ lineHeight: 1.5 }}>
        <code>ops_perm.role_scope</code>: the role-level cap on every filter&apos;s pickable
        values, resolved as <strong>role_scope ∩ user_scope</strong> (e.g.
        <code> geography=[OR]</code>, <code>programme=[HIV]</code>). A dimension left empty is
        <strong> unrestricted</strong>. Per-user tightening lives on the user (Users and Access).
        Runtime edits here replace the role&apos;s runtime rows; seed-managed rows are re-asserted
        by the loader.
      </p>
      <table className="dash">
        <thead><tr><th>Dimension</th><th>Allowed values (empty = unrestricted)</th></tr></thead>
        <tbody>
          {SCOPE_DIMENSIONS.map(dim => (
            <tr key={dim} data-testid={`scope-dim-${dim}`}>
              <td><code>{dim}</code></td>
              <td>
                <input style={{ width: '100%' }} disabled={disabled}
                       data-testid={`scope-input-${dim}`}
                       placeholder="unrestricted (comma,separated,values)"
                       value={(draft.scope[dim] ?? []).join(', ')}
                       onChange={e => setDim(dim, e.target.value.split(',').map(x => x.trim()).filter(Boolean))} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* user assignment (POST/DELETE /user-roles) */
type DirUser = { kc_sub: string; username: string; full_name: string | null };

function UserAssignPanel({ detail, onChanged }: {
  detail: api.RoleDetail; onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [dir, setDir] = useState<DirUser[] | null>(null);
  const [pick, setPick] = useState('');
  const [rawSub, setRawSub] = useState('');

  // No flat user-list endpoint, so build a directory from every role's holders
  // (same source UsersAccess uses). Fetched lazily on first "assign".
  const loadDir = useCallback(async () => {
    if (dir) return;
    try {
      const roles = await api.listRoles();
      const details = await Promise.all(roles.map(r => api.getRole(r.id).catch(() => null)));
      const byUser = new Map<string, DirUser>();
      details.forEach(d => d?.users.forEach(u =>
        byUser.set(u.kc_sub, { kc_sub: u.kc_sub, username: u.username, full_name: u.full_name })));
      setDir([...byUser.values()].sort((a, b) => (a.full_name || a.username).localeCompare(b.full_name || b.username)));
    } catch (e: any) { setErr(e?.message || String(e)); }
  }, [dir]);

  const holders = detail.users;
  const holderSubs = new Set(holders.map(u => u.kc_sub));

  async function assign(kc_sub: string) {
    if (!kc_sub) return;
    setBusy(true); setErr(null);
    try { await api.grantUserRole(kc_sub, detail.id); setPick(''); setRawSub(''); onChanged(); }
    catch (e: any) { setErr(e?.message || String(e)); }
    finally { setBusy(false); }
  }
  async function revoke(kc_sub: string, label: string) {
    if (!window.confirm(`Revoke ${detail.code} from ${label}?`)) return;
    setBusy(true); setErr(null);
    try { await api.revokeUserRole(kc_sub, detail.id); onChanged(); }
    catch (e: any) { setErr(e?.message || String(e)); }
    finally { setBusy(false); }
  }

  const kit = useTableKit(holders, {
    pageSize: 6,
    search: u => `${u.full_name ?? ''} ${u.username} ${u.email ?? ''} ${u.source}`,
    sortVals: { user: u => u.full_name || u.username, status: u => (u.is_active ? 0 : 1) },
    initialSort: { key: 'user', dir: 'asc' },
  });

  return (
    <div>
      <h5 className="mt-md">Assigned users <span className="text-xs muted">({holders.filter(u => u.is_active).length} active)</span></h5>
      {err && <p className="text-xs" style={{ color: 'var(--alert)' }}>{err}</p>}
      {holders.length > 0 ? (
        <>
          <TableToolbar kit={kit} placeholder="Search users…" unit="users" />
          <div className="tk-scroll">
            <table className="dash">
              <thead><tr><SortTh kit={kit} k="user">User</SortTh><th>Source</th><SortTh kit={kit} k="status">Status</SortTh><th></th></tr></thead>
              <tbody>
                {kit.pageRows.map(u => (
                  <tr key={u.kc_sub} data-testid={`holder-${u.kc_sub}`}>
                    <td><strong>{u.full_name || u.username}</strong><br /><span className="text-xs muted">{u.email || u.kc_sub}</span></td>
                    <td><span className="text-xs muted">{u.source}</span></td>
                    <td><span className={`pill ${u.is_active ? 'good' : 'neutral'}`}>{u.is_active ? 'active' : 'revoked'}</span></td>
                    <td>{u.is_active && <button className="btn btn-sm btn-ghost" disabled={busy} onClick={() => revoke(u.kc_sub, u.full_name || u.username)}>Revoke</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <TablePager kit={kit} />
        </>
      ) : <p className="muted text-sm">No users hold this role yet.</p>}

      <div className="flex-row gap-sm" style={{ alignItems: 'center', flexWrap: 'wrap', marginTop: '0.5rem' }}>
        <select value={pick} disabled={busy} onFocus={loadDir} data-testid="assign-user-select"
                onChange={e => setPick(e.target.value)} style={{ minWidth: 220 }}>
          <option value="">{dir ? '＋ assign existing user…' : '＋ assign user (load directory)…'}</option>
          {(dir ?? []).filter(u => !holderSubs.has(u.kc_sub)).map(u =>
            <option key={u.kc_sub} value={u.kc_sub}>{u.full_name || u.username}</option>)}
        </select>
        <button className="btn btn-sm btn-primary" disabled={busy || !pick} onClick={() => assign(pick)}>Assign</button>
        <span className="text-xs muted">or by kc_sub:</span>
        <input placeholder="uuid" value={rawSub} disabled={busy} onChange={e => setRawSub(e.target.value.trim())} style={{ width: 260 }} />
        <button className="btn btn-sm btn-ghost" disabled={busy || !rawSub} onClick={() => assign(rawSub)}>Assign sub</button>
      </div>
    </div>
  );
}

/* the editor pane: composes every axis + Save/Cancel */
function RoleDetailEditor({ catalog, detail, onSaved, onDeleted }: {
  catalog: api.Catalog | null;
  detail: api.RoleDetail;
  onSaved: () => void;
  onDeleted: () => void;
}) {
  const baseline = useMemo(() => draftFromDetail(detail), [detail]);
  const [draft, setDraft] = useState<Draft>(baseline);
  useEffect(() => { setDraft(draftFromDetail(detail)); }, [detail]);

  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const patch = useCallback((p: Partial<Draft>) => { setSaved(null); setDraft(d => ({ ...d, ...p })); }, []);

  const metaDirty = canonMeta(draft) !== canonMeta(baseline);
  const grantsDirty = canonGrants(draft) !== canonGrants(baseline);
  const dirty = metaDirty || grantsDirty;

  async function save() {
    setBusy(true); setErr(null); setSaved(null);
    try {
      if (metaDirty) {
        await api.updateRole(detail.id, {
          code: detail.code, name: draft.name.trim(),
          description: draft.description.trim() || null,
          combine_strategy: draft.combine_strategy,
          capabilities: draft.capabilities,
        });
      }
      let rev: number | undefined;
      if (grantsDirty) {
        const res = await api.replaceGrants(detail.id, {
          dashboards: draft.dashboards,
          widgets: draft.widgets,
          scope: draft.scope,               // role_scope caps
        });
        rev = res.revision_id;
      }
      setSaved(rev != null ? `Saved · policy revision #${rev}` : 'Saved.');
      onSaved();
    } catch (e: any) { setErr(e?.message || String(e)); }
    finally { setBusy(false); }
  }

  async function del() {
    const active = detail.users.filter(u => u.is_active).length;
    const msg = active > 0
      ? `${detail.code} still has ${active} active user${active > 1 ? 's' : ''}. Deleting REVOKES it from all of them and cascades every grant. Continue?`
      : `Delete role ${detail.code}? This cascades to all grants.`;
    if (!window.confirm(msg)) return;
    setBusy(true); setErr(null);
    try { await api.deleteRole(detail.id); onDeleted(); }
    catch (e: any) { setErr(e?.message || String(e)); }
    finally { setBusy(false); }
  }

  return (
    <div className="card card-elev" data-testid="role-editor">
      <div className="card-hdr">
        <h4><code>{detail.code}</code> · {draft.name || detail.name}</h4>
        <button className="btn btn-sm btn-ghost" onClick={del} disabled={busy} data-testid="role-delete">
          <svg className="ico ico-sm"><use href="#i-x" /></svg> Delete role
        </button>
      </div>

      <OpaWarningBanner code={detail.code} />
      {err && <div className="alert-banner mb-sm" style={{ background: 'var(--alert-50)', color: 'var(--alert)' }}>{err}
        <button className="btn btn-sm btn-ghost" onClick={() => setErr(null)} style={{ marginLeft: 'auto' }}>Dismiss</button></div>}

      <MetaCapEditor code={detail.code} draft={draft} patch={patch} disabled={busy} />
      <DashboardGrantEditor catalog={catalog} draft={draft} patch={patch} disabled={busy} />
      <WidgetGrantEditor catalog={catalog} draft={draft} patch={patch} disabled={busy} />
      <ScopeEditor draft={draft} patch={patch} disabled={busy} />
      <UserAssignPanel detail={detail} onChanged={onSaved} />

      {/* sticky Save/Cancel bar */}
      <div className="flex-row gap-sm" data-testid="role-save-bar"
           style={{ position: 'sticky', bottom: 0, marginTop: '1rem', padding: '0.7rem 0',
                    background: 'var(--surface, #fff)', borderTop: '1px solid var(--line, #e6e0d6)',
                    alignItems: 'center' }}>
        {dirty
          ? <span className="pill watch" data-testid="dirty-flag">unsaved: {[metaDirty && 'metadata', grantsDirty && 'grants'].filter(Boolean).join(' + ')}</span>
          : <span className="text-xs muted">{saved ?? 'No unsaved changes.'}</span>}
        <span className="tk-spacer" style={{ flex: 1 }} />
        <button className="btn btn-sm btn-ghost" disabled={busy || !dirty} data-testid="role-cancel"
                onClick={() => { setDraft(draftFromDetail(detail)); setSaved(null); }}>Cancel</button>
        <button className="btn btn-sm btn-primary" disabled={busy || !dirty} data-testid="role-save" onClick={save}>
          {busy ? 'Saving…' : 'Save changes'}
        </button>
      </div>
    </div>
  );
}

/* new-role create form */
function NewRoleForm({ onCreated, onCancel }: {
  onCreated: (id: number) => void; onCancel: () => void;
}) {
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [caps, setCaps] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const codeUp = code.trim().toUpperCase();
  const valid = codeUp.length >= 3 && name.trim().length > 0;

  async function create() {
    setBusy(true); setErr(null);
    try {
      const r = await api.createRole({ code: codeUp, name: name.trim(), description: description.trim() || null, capabilities: caps });
      onCreated(r.id);
    } catch (e: any) { setErr(e?.message || String(e)); }
    finally { setBusy(false); }
  }

  return (
    <div className="card card-elev" data-testid="new-role-form">
      <div className="card-hdr"><h4><svg className="ico ico-sm"><use href="#i-plus" /></svg> New role</h4></div>
      {err && <div className="alert-banner mb-sm" style={{ background: 'var(--alert-50)', color: 'var(--alert)' }}>{err}</div>}
      {codeUp.length >= 3 && <OpaWarningBanner code={codeUp} />}
      <div className="grid grid-2 gap-lg" style={{ alignItems: 'start' }}>
        <div className="field-group">
          <label>Role code <span className="text-xs muted">(immutable · e.g. R-PD-TB)</span></label>
          <input value={code} data-testid="new-role-code" placeholder="R-…" onChange={e => setCode(e.target.value)} />
        </div>
        <div className="field-group">
          <label>Display name</label>
          <input value={name} data-testid="new-role-name" onChange={e => setName(e.target.value)} />
        </div>
      </div>
      <div className="field-group">
        <label>Description</label>
        <textarea rows={2} value={description} onChange={e => setDescription(e.target.value)} />
      </div>
      <h5 className="mt-sm">Capabilities <span className="text-xs muted">(optional)</span></h5>
      <CapabilityPicker selected={caps} onToggle={c => setCaps(caps.includes(c) ? caps.filter(x => x !== c) : [...caps, c])} disabled={busy} />
      <div className="flex-row gap-sm" style={{ marginTop: '1rem' }}>
        <span className="tk-spacer" style={{ flex: 1 }} />
        <button className="btn btn-sm btn-ghost" onClick={onCancel} disabled={busy}>Cancel</button>
        <button className="btn btn-sm btn-primary" onClick={create} disabled={busy || !valid} data-testid="new-role-create">
          {busy ? 'Creating…' : 'Create role'}
        </button>
      </div>
      <p className="text-xs muted" style={{ marginTop: '0.6rem' }}>
        Creating a role writes the DB grant model only. Assign its 4-axis grants after creation;
        add its rego policy separately for OPA enforcement.
      </p>
    </div>
  );
}

/* permission-change audit trail (GET /audit) */
function auditTarget(pk: any): string {
  if (!pk || typeof pk !== 'object') return String(pk ?? 'n/a');
  return Object.entries(pk).map(([k, v]) => `${k}=${v}`).join(' · ');
}

function AuditPanel() {
  const [rows, setRows] = useState<api.AuditRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [denied, setDenied] = useState(false);

  const load = useCallback(async () => {
    setErr(null); setDenied(false);
    try { setRows(await api.auditLog(100)); }
    catch (e: any) {
      const m = e?.message || String(e);
      if (/^40[13]\b/.test(m)) setDenied(true); else setErr(m);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const kit = useTableKit(rows, {
    pageSize: 10,
    search: r => `${r.actor_username ?? r.actor_sub ?? ''} ${r.table_name} ${r.op} ${r.revision_note ?? ''} ${auditTarget(r.pk)}`,
    sortVals: { ts: r => r.ts, table: r => r.table_name, op: r => r.op, rev: r => r.revision_id ?? 0 },
    initialSort: { key: 'ts', dir: 'desc' },
  });

  return (
    <div className="page-section" data-testid="audit-panel">
      <div className="ps-hdr">
        <div>
          <h3 className="ps-title"><svg className="ico"><use href="#i-list" /></svg> Permission-change audit</h3>
          <p className="ps-sub">Every edit above lands one <code>audit_permission_change</code> row tied to a <code>policy_revision</code>. Newest first.</p>
        </div>
        <div className="ps-meta"><button className="btn btn-sm btn-ghost" onClick={load}><svg className="ico ico-sm"><use href="#i-refresh" /></svg> Refresh</button></div>
      </div>

      {denied && <div className="card card-elev"><p className="text-sm muted" style={{ margin: '0.4rem 0' }}>
        <svg className="ico ico-sm"><use href="#i-lock" /></svg> The gateway declined the audit trail for your role (needs <code>manage_users</code>).
      </p></div>}
      {err && <p className="text-xs" style={{ color: 'var(--alert)' }}>Failed to load audit: {err}</p>}
      {!denied && (
        <div className="card card-elev" style={{ paddingTop: '0.8rem' }}>
          {!rows && !err && <p className="muted">Loading audit trail…</p>}
          {rows && rows.length === 0 && <p className="muted">No permission changes recorded yet.</p>}
          {rows && rows.length > 0 && (
            <>
              <TableToolbar kit={kit} placeholder="Search actor, table, op, revision…" unit="changes" />
              <div className="tk-scroll">
                <table className="dash">
                  <thead><tr>
                    <SortTh kit={kit} k="ts">When</SortTh>
                    <th>Actor</th>
                    <SortTh kit={kit} k="table">Table</SortTh>
                    <SortTh kit={kit} k="op">Op</SortTh>
                    <th>Target</th>
                    <SortTh kit={kit} k="rev">Revision</SortTh>
                  </tr></thead>
                  <tbody>
                    {kit.pageRows.map(r => (
                      <tr key={r.id}>
                        <td title={r.ts}>{fmtAgo(r.ts)}</td>
                        <td>{r.actor_username
                          ? <strong className="text-sm">{r.actor_username}</strong>
                          : r.actor_sub ? <code className="text-xs">{r.actor_sub.slice(0, 8)}</code>
                          : <span className="muted">n/a</span>}</td>
                        <td><code className="text-xs">{r.table_name}</code></td>
                        <td><span className={`pill ${r.op === 'delete' ? 'alert' : r.op === 'insert' ? 'good' : 'info'}`}>{r.op}</span></td>
                        <td className="text-xs">{auditTarget(r.pk)}</td>
                        <td title={r.revision_note ?? ''}>{r.revision_id != null ? <>#{r.revision_id} <span className="text-xs muted">{r.revision_note}</span></> : <span className="muted">n/a</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <TablePager kit={kit} />
            </>
          )}
        </div>
      )}
    </div>
  );
}

/* 3 Policy (OPA) */

// Real role codes the OPA decision router (7_security/opa/policies/common.rego)
// recognizes. The evaluator simulates "as" one of these principals.
const OPA_ROLES: Array<[string, string]> = [
  ['R-SUPER',        'Superuser'],
  ['R-DEC',          'Decision-maker · national'],
  ['R-PD-HIV',       'Programme director · HIV (scoped)'],
  ['R-RHB-OROMIA',   'RHB director · Oromia (scoped)'],
  ['R-CONS-ANA',     'Analyst'],
  ['R-CONS-SCI',     'Scientist'],
  ['R-CONS-RES',     'Researcher'],
  ['R-CONS-PRT',     'Partner'],
  ['R-STEW',         'Data steward'],
  ['R-PROD-CLIN',    'Clinical producer'],
  ['R-PROD-SUBMIT',  'Submission producer'],
  ['R-OPS-ENG',      'Ops engineer'],
  ['R-OPS-ADM',      'Ops admin'],
  ['R-OPS-SEC',      'Ops security'],
  ['R-PUB-ANON',     'Anonymous public'],
];

// Real resource subjects from the Rego permission tables (input.resource.subject).
const OPA_RESOURCES: Array<[string, string]> = [
  ['PT',  'PT · patient records'],
  ['PRG', 'PRG · programme indicators'],
  ['FAC', 'FAC · facility directory'],
  ['HP',  'HP · health professionals'],
  ['PRD', 'PRD · supply commodities'],
];

// Real action verbs (input.action.verb): read plus the sensitive-verb set.
const OPA_ACTIONS = ['read', 'export', 'schedule', 'alert', 'annotate', 'write', 'delete'];

function fmtAgo(iso: string | null): string {
  if (!iso) return 'n/a';
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return 'just now';
  if (ms < 60_000)     return `${Math.round(ms / 1000)}s ago`;
  if (ms < 3_600_000)  return `${Math.round(ms / 60_000)}m ago`;
  if (ms < 86_400_000) return `${Math.round(ms / 3_600_000)}h ago`;
  return `${Math.round(ms / 86_400_000)}d ago`;
}

function PolicyPanel() {
  // Test-policy evaluator
  const [role, setRole]         = useState(OPA_ROLES[2][0]);      // R-PD-HIV
  const [resource, setResource] = useState(OPA_RESOURCES[0][0]);  // PT
  const [action, setAction]     = useState(OPA_ACTIONS[0]);       // read
  const [busy, setBusy]         = useState(false);
  const [evalErr, setEvalErr]   = useState<string | null>(null);
  const [result, setResult]     = useState<policy.PolicyDecision | null>(null);

  const evaluate = async () => {
    if (busy) return;
    setBusy(true); setEvalErr(null); setResult(null);
    try {
      const d = await policy.evaluatePolicy({
        action, resource,
        principal: { primary_role: role, roles: [role] },
      });
      setResult(d);
    } catch (e: any) {
      setEvalErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-section">
      <div className="ps-hdr">
        <div>
          <h3 className="ps-title"><svg className="ico"><use href="#i-policy" /></svg> Policy enforcement (OPA)</h3>
          <p className="ps-sub">Row-level + column-level security · OPA-enforced per-role Rego bundles · evaluated at the query layer.</p>
        </div>
      </div>

      <div style={{ maxWidth: 560 }}>
        {/* Evaluator: POST /gw/policy/evaluate */}
        <div className="card card-elev">
          <div className="card-hdr"><h4>Test policy</h4><span className="meta">Simulate access for a role by resource</span></div>
          <div className="field-group">
            <label>Role (simulate as)</label>
            <select value={role} onChange={e => setRole(e.target.value)}>
              {OPA_ROLES.map(([code, lbl]) => <option key={code} value={code}>{lbl} · {code}</option>)}
            </select>
          </div>
          <div className="field-group">
            <label>Resource</label>
            <select value={resource} onChange={e => setResource(e.target.value)}>
              {OPA_RESOURCES.map(([code, lbl]) => <option key={code} value={code}>{lbl}</option>)}
            </select>
          </div>
          <div className="field-group">
            <label>Action</label>
            <select value={action} onChange={e => setAction(e.target.value)}>
              {OPA_ACTIONS.map(a => <option key={a} value={a}>{a}</option>)}
            </select>
          </div>
          <button className="btn btn-primary" disabled={busy} onClick={evaluate}>
            {busy ? 'Evaluating…' : 'Evaluate'}
          </button>

          {evalErr && (
            <div className="text-xs" style={{ marginTop: '1rem', color: 'var(--alert)' }}>
              Evaluation failed: {evalErr}
            </div>
          )}

          {result && (
            <div style={{ marginTop: '1rem', padding: '0.95rem 1.1rem', background: '#fff', borderRadius: 'var(--radius-lg)', fontSize: '0.85rem', boxShadow: 'var(--shadow-1)' }}>
              <strong style={{ color: result.allow ? 'var(--good)' : 'var(--alert)' }}>
                <svg className="ico"><use href={result.allow ? '#i-check-circle' : '#i-x-circle'} /></svg>
                {' '}{result.allow ? 'ALLOWED' : 'DENIED'}
              </strong>
              {result.reason && <> · {result.reason}</>}
              {result.rule_hits.length > 0 && (
                <div className="mt-sm">Rules fired: {result.rule_hits.map((r, i) => <code key={i} style={{ marginRight: '0.4rem' }}>{r}</code>)}</div>
              )}
              {result.masked_columns.length > 0 && (
                <div className="mt-sm">Masked columns: {result.masked_columns.map((c, i) => <code key={i} style={{ marginRight: '0.4rem' }}>{c}</code>)}</div>
              )}
              {Object.keys(result.obligations).length > 0 && (
                <div className="text-xs muted mt-sm">Obligations: <code>{JSON.stringify(result.obligations)}</code></div>
              )}
              <div className="text-xs muted mt-sm">
                Evaluated for <code>{result.evaluated_for.primary_role}</code>
                {result.evaluated_for.geographies.length > 0 && <> · geo: {result.evaluated_for.geographies.join(', ')}</>}
                {result.evaluated_for.programs.length > 0 && <> · prog: {result.evaluated_for.programs.join(', ')}</>}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* the merged page */

function RolesPermissions() {
  const [catalog, setCatalog] = useState<api.Catalog | null>(null);
  const [catErr, setCatErr] = useState<string | null>(null);

  // one catalog fetch feeds the grants pane and both catalog panels.
  useEffect(() => {
    api.getCatalog().then(setCatalog).catch(e => setCatErr(e.message));
  }, []);

  return (
    <section id="roles-permissions">
      <p className="page-lede">
        The whole permission surface in one place: role definitions and their 4-axis grants,
        the permission-change audit trail, and the OPA enforcement layer with a live policy tester.
        The widget &amp; filter catalogs now live under <strong>Dashboard Control</strong>.
      </p>

      {catErr && (
        <div className="alert-banner" style={{ background: 'var(--alert-50)', color: 'var(--alert)' }}>
          Catalog failed to load: {catErr}
        </div>
      )}

      <RolesGrants catalog={catalog} />
      <AuditPanel />
      <PolicyPanel />
    </section>
  );
}

/* widget-module registration (admin catalog runtime)
   One complex surface is one grantable widget: this module is the page's single catalog widget. */
import type { AdminWidget } from '../../runtime/types';
const widget: AdminWidget = {
  id: 'adm-roles-permissions',
  Component: RolesPermissions,
};
export default widget;
