/**
 * SSO & identity page: live CRUD over ops_identity.identity_source.
 *
 * A live card grid backed by /gw/perm-admin/identity-sources; each card edits
 * in a modal form.
 *
 * Saving here does not create or update the IdP in Keycloak: the backend's
 * Keycloak Admin API call is a stub that logs a warning.
 */
import { useEffect, useMemo, useState } from 'react';
import * as api from '../../api/permAdmin';

// Icon per IdP kind.
function iconFor(kind: string, code: string): string {
  if (code === 'fayda') return 'i-shield-check';
  if (code === 'moh-staff-direct') return 'i-building';
  if (code.includes('ehia')) return 'i-hospital';
  if (code.includes('who')) return 'i-globe';
  if (code.includes('aau')) return 'i-graduation';
  if (kind === 'social' && code === 'google') return 'i-globe';
  if (kind === 'social' && code === 'microsoft') return 'i-globe';
  return 'i-lock';
}

function statusPill(status: string): 'good' | 'watch' | 'neutral' {
  if (status === 'active') return 'good';
  if (status === 'available') return 'neutral';
  return 'watch';
}

const KIND_OPTS: Array<api.IdentitySourceIn['kind']> = ['direct', 'oidc', 'saml', 'social', 'fayda'];
const STATUS_OPTS: Array<NonNullable<api.IdentitySourceIn['status']>> = ['active', 'available', 'disabled'];

type EditorState = {
  open: boolean;
  editing: api.IdentitySource | null;     // null = create
  form: api.IdentitySourceIn;
  configRaw: string;                      // textarea for config_json
};

const BLANK: api.IdentitySourceIn = {
  code: '', name: '', kind: 'oidc',
  protocol: 'oidc', status: 'available',
  config_json: null, keycloak_alias: null,
};

function SSO() {
  const [sources, setSources] = useState<api.IdentitySource[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editor, setEditor] = useState<EditorState>({
    open: false, editing: null, form: BLANK, configRaw: '',
  });

  async function reload() {
    try { setSources(await api.listIdentitySources()); }
    catch (e: any) { setErr(e.message); }
  }
  useEffect(() => { reload(); }, []);

  function openEdit(src: api.IdentitySource | null) {
    if (src) {
      setEditor({
        open: true, editing: src,
        form: {
          code: src.code, name: src.name,
          kind: src.kind as any, protocol: (src.protocol as any) || null,
          status: src.status, config_json: src.config_json,
          keycloak_alias: src.keycloak_alias,
        },
        configRaw: src.config_json ? JSON.stringify(src.config_json, null, 2) : '',
      });
    } else {
      setEditor({ open: true, editing: null, form: { ...BLANK }, configRaw: '' });
    }
  }

  function close() { setEditor(e => ({ ...e, open: false })); }

  async function save() {
    let config_json: any = null;
    if (editor.configRaw.trim()) {
      try { config_json = JSON.parse(editor.configRaw); }
      catch (e: any) { setErr('config_json must be valid JSON: ' + e.message); return; }
    }
    const body: api.IdentitySourceIn = { ...editor.form, config_json };
    setBusy(true);
    try {
      if (editor.editing) await api.updateIdentitySource(editor.editing.id, body);
      else                 await api.createIdentitySource(body);
      await reload();
      close();
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  }

  async function remove(src: api.IdentitySource) {
    if (!window.confirm(`Delete identity source "${src.code}"? Users linked to it will lose this login path.`)) return;
    setBusy(true);
    try {
      await api.deleteIdentitySource(src.id);
      await reload();
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  }

  async function toggleStatus(src: api.IdentitySource) {
    const next: api.IdentitySource['status'] =
      src.status === 'active' ? 'disabled' :
      src.status === 'available' ? 'active' :
      'available';
    setBusy(true);
    try {
      await api.updateIdentitySource(src.id, {
        code: src.code, name: src.name,
        kind: src.kind as any, protocol: (src.protocol as any) || null,
        status: next, config_json: src.config_json,
        keycloak_alias: src.keycloak_alias,
      });
      await reload();
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  }

  const counts = useMemo(() => {
    const c = { total: 0, active: 0, available: 0, disabled: 0 } as Record<string, number>;
    (sources ?? []).forEach(s => { c.total++; (c as any)[s.status] = ((c as any)[s.status] || 0) + 1; });
    return c;
  }, [sources]);

  return (
    <section>
      <p className="page-lede">
        Federated identity providers. Active providers appear as login options
        on every dashboard. Edits land in <code>ops_identity.identity_source</code>;
        Keycloak Admin API sync is not implemented yet.
      </p>

      {err && (
        <div className="alert-banner" style={{ background: 'var(--alert-50)', color: 'var(--alert)' }}>
          {err}
          <button className="btn btn-sm btn-ghost" onClick={() => setErr(null)} style={{ marginLeft: 'auto' }}>Dismiss</button>
        </div>
      )}

      <div className="section-hdr">
        <h3>Identity sources <span className="text-xs muted">({counts.total} total · {counts.active || 0} active · {counts.available || 0} available · {counts.disabled || 0} disabled)</span></h3>
        <div className="flex-row gap-sm">
          <button className="btn btn-sm btn-primary" onClick={() => openEdit(null)} disabled={busy}>
            <svg className="ico ico-sm"><use href="#i-plus" /></svg> New IdP
          </button>
        </div>
      </div>

      {!sources && <p className="muted">Loading…</p>}

      <div className="grid grid-3">
        {(sources ?? []).map(s => (
          <div key={s.id} className="card card-elev">
            <div className="card-hdr">
              <h4><svg className="ico"><use href={'#' + iconFor(s.kind, s.code)} /></svg> {s.name}</h4>
              <span className={`pill ${statusPill(s.status)}`}>{s.status}</span>
            </div>
            <p className="text-sm muted mt-sm">
              <code>{s.code}</code> · {s.kind}
              {s.protocol && <> · {s.protocol.toUpperCase()}</>}
            </p>
            <ul className="text-xs muted mt-sm" style={{ paddingLeft: '1.0rem' }}>
              {s.keycloak_alias && <li>Keycloak alias: <code>{s.keycloak_alias}</code></li>}
              {s.config_json?.realm && <li>Realm: <code>{s.config_json.realm}</code></li>}
              {s.config_json?.issuer_url && <li>Issuer: <code>{s.config_json.issuer_url}</code></li>}
              {s.config_json?.idp_entity_id && <li>EntityID: <code>{s.config_json.idp_entity_id}</code></li>}
              {s.config_json?.client_id && <li>Client ID: <code>{s.config_json.client_id}</code></li>}
              {s.config_json?.purpose && <li className="text-xs">{s.config_json.purpose.slice(0, 100)}{s.config_json.purpose.length > 100 ? '…' : ''}</li>}
            </ul>
            <div className="flex-row mt-md" style={{ gap: '0.4rem' }}>
              <button className="btn btn-sm btn-primary" onClick={() => openEdit(s)} disabled={busy}>Configure</button>
              <button className="btn btn-sm btn-ghost" onClick={() => toggleStatus(s)} disabled={busy}>
                {s.status === 'active' ? 'Disable' : 'Activate'}
              </button>
              <span className="lc-spacer"></span>
              <button className="btn btn-sm btn-ghost" onClick={() => remove(s)} disabled={busy} title="Delete">
                <svg className="ico ico-sm"><use href="#i-x" /></svg>
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* editor modal */}
      {editor.open && (
        <div className="modal-bg" onClick={(e) => { if (e.target === e.currentTarget) close(); }}>
          <div className="modal" style={{ maxWidth: '640px' }}>
            <div className="modal-hdr">
              <h2><svg className="ico"><use href="#i-lock" /></svg> {editor.editing ? 'Configure identity source' : 'New identity source'}</h2>
              <p>{editor.editing ? `Editing ${editor.editing.code}` : 'Register a new federated IdP'}</p>
            </div>
            <div className="modal-body">
              <div className="grid grid-2 gap-lg">
                <div className="field-group">
                  <label>Code <span className="text-xs muted">(unique)</span></label>
                  <input value={editor.form.code}
                         onChange={(e) => setEditor(s => ({ ...s, form: { ...s.form, code: e.target.value } }))}
                         placeholder="e.g. ehia-saml" />
                </div>
                <div className="field-group">
                  <label>Display name</label>
                  <input value={editor.form.name}
                         onChange={(e) => setEditor(s => ({ ...s, form: { ...s.form, name: e.target.value } }))} />
                </div>
                <div className="field-group">
                  <label>Kind</label>
                  <select value={editor.form.kind}
                          onChange={(e) => setEditor(s => ({ ...s, form: { ...s.form, kind: e.target.value as any } }))}>
                    {KIND_OPTS.map(k => <option key={k} value={k}>{k}</option>)}
                  </select>
                </div>
                <div className="field-group">
                  <label>Protocol</label>
                  <select value={editor.form.protocol || ''}
                          onChange={(e) => setEditor(s => ({ ...s, form: { ...s.form, protocol: (e.target.value || null) as any } }))}>
                    <option value="">(none)</option>
                    <option value="oidc">OIDC</option>
                    <option value="saml">SAML 2.0</option>
                  </select>
                </div>
                <div className="field-group">
                  <label>Status</label>
                  <select value={editor.form.status || 'available'}
                          onChange={(e) => setEditor(s => ({ ...s, form: { ...s.form, status: e.target.value as any } }))}>
                    {STATUS_OPTS.map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
                <div className="field-group">
                  <label>Keycloak alias <span className="text-xs muted">(optional)</span></label>
                  <input value={editor.form.keycloak_alias || ''}
                         onChange={(e) => setEditor(s => ({ ...s, form: { ...s.form, keycloak_alias: e.target.value || null } }))}
                         placeholder="e.g. ehia-saml" />
                </div>
              </div>
              <div className="field-group">
                <label>config_json <span className="text-xs muted">(JSON · realm / issuer_url / client_id / attribute_mappings / …)</span></label>
                <textarea rows={8} style={{ fontFamily: 'var(--font-mono)', fontSize: '0.78rem' }}
                          value={editor.configRaw}
                          onChange={(e) => setEditor(s => ({ ...s, configRaw: e.target.value }))}
                          placeholder='{\n  "realm": "moh-staff",\n  "issuer_url": "https://idp.example/realms/x"\n}'/>
              </div>
              <p className="text-xs muted">
                <strong>Not yet synced</strong>: saving here updates the DB
                catalog only. The Keycloak Admin API call that actually
                creates or updates the IdP in Keycloak is not implemented yet.
              </p>
            </div>
            <div className="modal-foot">
              <button className="btn btn-ghost" onClick={close} disabled={busy}>Cancel</button>
              <button className="btn btn-primary" onClick={save} disabled={busy || !editor.form.code || !editor.form.name}>
                <svg className="ico"><use href="#i-check" /></svg> {busy ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

/* widget-module registration (admin catalog runtime)
   1:1; page admin/sso has exactly this widget. */
import type { AdminWidget } from '../../runtime/types';
const widget: AdminWidget = {
  id: 'adm-sso-cards',
  Component: SSO,
};
export default widget;
