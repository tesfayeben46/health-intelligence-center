/**
 * Documentation (page id admin/documentation, widget adm-documentation): the
 * operator's reference page. It brings together:
 *   · live deployment facts + gateway feature flags (GET /gw/observability/env),
 *   · the protected gateway API reference (Swagger UI at the gateway /docs),
 *   · the curated system docs, served from the object store (SeaweedFS
 *     bucket nhdw-docs) via GET /gw/docs; each opens in an in-app markdown
 *     viewer or downloads,
 *   · the operator tool deep links (each opens on the existing Keycloak session).
 *
 * The env and docs feeds show an explicit state when down, and no tool tile
 * points at a host that is not in the ingress.
 */
import { useEffect, useState } from 'react';
import { fetchEnv, type EnvResp, type EnvInfo } from '../../api/env';
import { fetchDocs, fetchDoc, fetchDocDownloadUrl, type DocMeta } from '../../api/docs';
import '../../styles/documentation.css';

// Operator-tool hosts follow the host this admin SPA is served on (admin.<base> -> <sub>.<base>),
// so the whole system re-homes on a single hostBase with no rebuild. No window (SSR) -> dev fallback.
const HOST_BASE = (typeof window !== 'undefined' ? window.location.host.replace(/^admin\./, '') : '127.0.0.1.nip.io');
const toolUrl = (sub: string, path = ''): string => `https://${sub}.${HOST_BASE}${path}`;


const REALM = 'privileged-ops';
const SWAGGER_URL = toolUrl('gateway', '/docs');

const ENV_FIELDS: Array<[keyof EnvInfo, string, 'strong' | 'code' | 'text']> = [
  ['name',            'Name',          'strong'],
  ['namespace',       'Namespace',     'code'],
  ['base_domain',     'Base domain',   'code'],
  ['tls',             'TLS',           'text'],
  ['gateway_version', 'Gateway build', 'code'],
];
function renderVal(v: string, as: 'strong' | 'code' | 'text') {
  if (as === 'strong') return <strong>{v}</strong>;
  if (as === 'code')   return <code>{v}</code>;
  return v;
}

/** Current user's R-* roles, decoded from the access token already on window
 *  (no extra fetch): mirrors the workshop's ws-help-tools. Used to hide a tool
 *  tile the signed-in role isn't permitted (external-tool SSO spec req 2). */
function currentRoles(): string[] {
  try {
    const t = window.__nhdwAccessToken;
    if (!t) return [];
    const p = JSON.parse(atob(t.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    const rr = (p.realm_access?.roles ?? []) as string[];
    const flat = (p.roles ?? []) as string[];
    return [...new Set([...rr, ...flat])].filter((r: string) => r.startsWith('R-'));
  } catch { return []; }
}

/** Operator tools: every URL's hostname exists in overlays/dev/ingress.yaml.
 *  Tools fronted by a tool-gate enter via /oauth2gate/login?realm=…&rd=… so the
 *  existing privileged-ops session admits silently; `roles`, when present, must
 *  mirror that gate's ALLOWED_ROLES so a shown tile never lands on the 403 page
 *  (omitted means every role that can see this admin page may use the tool). */
const TOOLS: Array<{ name: string; ico: string; brand?: string; desc: string; url: string; auth: string; roles?: string[] }> = [
  { name: 'dbt Docs', brand: '/static/assets/brand/dbt.svg',      ico: 'i-database', desc: 'Model lineage graph, column-level docs and the compiled DAG for every staging, vault and mart model.', url: toolUrl('dbt-docs', `/?realm=${REALM}`), auth: 'OIDC · Keycloak' },
  { name: 'Airflow', brand: '/static/assets/brand/airflow.svg',       ico: 'i-refresh',  desc: 'Pipeline & ML batch-inference orchestrator: DAG runs, task logs and schedules.', url: toolUrl('airflow', '/'), auth: 'OIDC · Keycloak' },
  // MLflow's gate excludes the security-officer role (R-OPS-SEC): hide the tile
  // for it so it never 403s (gate ALLOWED_ROLES: R-CONS-*, R-OPS-ADM, R-SUPER).
  { name: 'MLflow', brand: '/static/assets/brand/mlflow.svg',        ico: 'i-microscope', desc: 'Experiment tracking, the model registry and run artifacts for the workshop ML packages.', url: toolUrl('mlflow', `/oauth2gate/login?realm=${REALM}&rd=/`), auth: 'OIDC · Keycloak', roles: ['R-OPS-ADM', 'R-SUPER'] },
  { name: 'Cluster console (Headlamp)', brand: '/static/assets/brand/kubernetes.svg', ico: 'i-tools', desc: 'Raw-Kubernetes control: edit/scale/exec on nhdw workloads (namespace edit), read-only elsewhere.', url: `/oauth2gate/login?realm=${REALM}&rd=/k8s/`, auth: 'OIDC · Keycloak (tool-gate)' },
  { name: 'Prometheus', brand: '/static/assets/brand/prometheus.svg',    ico: 'i-pulse',    desc: 'Metrics store and query UI for the observability plane the topology page reads from.', url: toolUrl('prometheus', `/oauth2gate/login?realm=${REALM}&rd=/`), auth: 'OIDC · Keycloak' },
  { name: 'Alertmanager', brand: '/static/assets/brand/prometheus.svg',  ico: 'i-bell',     desc: 'Alert routing and silences for the Prometheus alert rules (see Alert routing page).', url: toolUrl('alertmanager', `/oauth2gate/login?realm=${REALM}&rd=/`), auth: 'OIDC · Keycloak' },
  // Jupyter is a moh-staff (workshop) tool; a privileged-ops operator can't
  // authenticate to it. Only R-SUPER spans both realms, so gate the tile to it
  // (its real home is the workshop dashboard's tools).
  { name: 'Jupyter', brand: '/static/assets/brand/jupyter.svg',       ico: 'i-notebook', desc: 'The workshop research sandbox: notebooks over the governed marts.', url: toolUrl('jupyter', '/'), auth: 'OIDC · Keycloak (moh-staff)', roles: ['R-SUPER'] },
  // Superset is likewise a moh-staff analyst tool (self-service SQL/BI over the
  // marts via Trino). Same cross-realm gate as Jupyter: its home is the workshop.
  { name: 'Superset', brand: '/static/assets/brand/superset.svg',      ico: 'i-chart-bar', desc: 'Self-service SQL Lab + no-code chart/dashboard building over the marts (Trino).', url: toolUrl('superset', '/login/keycloak'), auth: 'OIDC · Keycloak (moh-staff)', roles: ['R-SUPER'] },
  { name: 'Keycloak', brand: '/static/assets/brand/keycloak.svg',      ico: 'i-lock',     desc: 'Identity provider admin console: realms, clients, users and identity-provider wiring.', url: toolUrl('keycloak', '/'), auth: 'Keycloak admin' },
  // OpenMetadata is a moh-staff tool (steward/catalog audience), with the same
  // cross-realm posture as Jupyter/Superset; R-STEW, R-OPS-ADM and R-SUPER may
  // open it.
  { name: 'OpenMetadata', brand: '/static/assets/brand/openmetadata.png',  ico: 'i-database', desc: 'Data catalog: searchable assets, business glossary, column-level lineage and classification tags across lake, staging, vault and marts.', url: toolUrl('openmetadata', '/'), auth: 'OIDC \u00b7 Keycloak (moh-staff)', roles: ['R-STEW', 'R-OPS-ADM', 'R-SUPER'] },
];

/** Minimal, dependency-free markdown to HTML for the in-app doc viewer. Handles
 *  the subset our docs use: fenced code, headings, hr, bullet/numbered lists,
 *  inline bold/code/links, tables (pass through as code), paragraphs. Input is
 *  HTML-escaped first, so this can never inject markup from the doc body. */
function mdToHtml(md: string): string {
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const inline = (s: string) => esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  const out: string[] = [];
  const lines = md.split('\n');
  let i = 0, inList = false;
  let para: string[] = [];
  const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para = []; } };
  const closeList = () => { if (inList) { out.push('</ul>'); inList = false; } };
  const flush = () => { flushPara(); closeList(); };
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith('```')) {
      flush();
      const buf: string[] = []; i++;
      while (i < lines.length && !lines[i].startsWith('```')) { buf.push(esc(lines[i])); i++; }
      i++;
      out.push(`<pre class="doc-code"><code>${buf.join('\n')}</code></pre>`);
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { flush(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue; }
    if (/^(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { flush(); out.push('<hr/>'); i++; continue; }
    const li = line.match(/^\s*[-*+]\s+(.*)$/) || line.match(/^\s*\d+\.\s+(.*)$/);
    if (li) { flushPara(); if (!inList) { out.push('<ul>'); inList = true; } out.push(`<li>${inline(li[1])}</li>`); i++; continue; }
    if (line.trim() === '') { flush(); i++; continue; }
    closeList(); para.push(line); i++;
  }
  flush();
  return out.join('\n');
}

function Documentation() {
  const [env, setEnv] = useState<EnvResp | null>(null);
  const [envErr, setEnvErr] = useState<string | null>(null);
  const [docs, setDocs] = useState<DocMeta[] | null>(null);
  const [docsErr, setDocsErr] = useState<string | null>(null);
  const [viewer, setViewer] = useState<{ title: string; name: string; html: string } | null>(null);
  const [loadingDoc, setLoadingDoc] = useState<string | null>(null);
  const [activeSec, setActiveSec] = useState<string>('all');   // System-docs section filter

  useEffect(() => {
    fetchEnv().then(d => { setEnv(d); setEnvErr(null); }).catch(e => setEnvErr(e.message || String(e)));
    fetchDocs().then(r => { setDocs(r.docs); setDocsErr(null); }).catch(e => setDocsErr(e.message || String(e)));
  }, []);

  const e = env?.environment;
  const flags = env?.feature_flags ?? [];

  async function openDoc(d: DocMeta) {
    setLoadingDoc(d.name);
    try {
      const c = await fetchDoc(d.name);
      setViewer({ title: d.title, name: d.name, html: mdToHtml(c.markdown) });
    } catch (err: any) {
      setViewer({ title: d.title, name: d.name, html: `<p class="muted">Could not load: ${err.message || err}</p>` });
    } finally { setLoadingDoc(null); }
  }
  async function downloadDoc(d: DocMeta) {
    try {
      const { url } = await fetchDocDownloadUrl(d.name);
      window.open(url, '_blank', 'noopener');
    } catch (err) { /* best-effort */ }
  }

  // group docs by section, preserving first-seen order
  const grouped: Array<[string, DocMeta[]]> = [];
  for (const d of docs ?? []) {
    let g = grouped.find(([s]) => s === d.section);
    if (!g) { g = [d.section, []]; grouped.push(g); }
    g[1].push(d);
  }

  return (
    <section className="doc-page">
      <p className="page-lede">
        The operator's reference: what this deployment is and how it's configured, where the API
        contract lives, the system documentation (served from the data lake, open or download any
        of them here), and every external tool one click away on your existing session.
      </p>

      {/* This deployment (full-width; env + flags side by side) */}
      <div className="card card-elev mt-md">
        <div className="card-hdr"><h4>This deployment</h4><span className="meta">live · /gw/observability/env</span></div>
        {envErr && <p className="text-xs" style={{ color: 'var(--alert)' }}>Environment feed unavailable: {envErr}</p>}
        {!env && !envErr && <p className="muted text-sm">Loading environment…</p>}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(280px,1fr))', gap: '1.2rem', alignItems: 'start' }}>
          {e && (
            <table className="dash mt-sm">
              <tbody>
                {ENV_FIELDS.filter(([k]) => e[k] != null).map(([k, label, as]) => (
                  <tr key={k}><td>{label}</td><td>{renderVal(String(e[k]), as)}</td></tr>
                ))}
              </tbody>
            </table>
          )}
          {env && (
            <div>
              <h4 style={{ fontSize: '.8rem', marginTop: '.5rem' }}>Feature flags</h4>
              {flags.length === 0 ? <p className="muted text-xs">No feature flags reported.</p> : (
                <table className="dash mt-sm">
                  <tbody>
                    {flags.map(f => (
                      <tr key={f.key}>
                        <td>{f.label}</td>
                        <td><span className={`pill ${f.state === 'on' ? 'good' : 'neutral'}`}>{f.state}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <p className="text-xs muted mt-sm">Read-only here · changes go through CI/CD.</p>
            </div>
          )}
        </div>
      </div>

      {/* API reference: compact clickable strip (no wasted card) */}
      <a className="card card-elev mt-md" href={SWAGGER_URL} target="_blank" rel="noreferrer"
         style={{ display: 'flex', alignItems: 'center', gap: '.85rem', padding: '.7rem 1rem', textDecoration: 'none' }}>
        <svg className="ico" style={{ flex: 'none', color: 'var(--navy-500)' }}><use href="#i-code" /></svg>
        <span style={{ flex: 1, minWidth: 0 }} className="text-sm">
          <strong>API reference</strong>. The gateway's full OpenAPI contract at <code>/docs</code>: every route,
          with schemas and a try-it-out console, behind your session.
        </span>
        <span className="text-xs muted" style={{ flex: 'none' }}>FastAPI · Swagger</span>
        <span className="btn btn-primary btn-sm" style={{ flex: 'none' }}>
          Open <svg className="ico ico-sm"><use href="#i-external" /></svg>
        </span>
      </a>

      {/* System documentation (files from the nhdw-docs bucket) */}
      <div className="page-section">
        <div className="ps-hdr">
          <div>
            <h3 className="ps-title"><svg className="ico"><use href="#i-book" /></svg> System documentation</h3>
            <p className="ps-sub">Served live from the data lake (<code>nhdw-docs</code>). Open any document in the viewer or download the file.</p>
          </div>
        </div>
        {docsErr && <p className="text-xs" style={{ color: 'var(--alert)' }}>Documentation feed unavailable: {docsErr}</p>}
        {!docs && !docsErr && <p className="muted text-sm">Loading documents…</p>}
        {docs && docs.length === 0 && <p className="muted text-sm">No documents published.</p>}
        {grouped.length > 0 && (() => {
          // one invisible container: a filter chip row switches the visible section
          // (no more a tall stack of every group). 'All' shows everything.
          const secs = grouped.map(([s]) => s);
          const sel = activeSec !== 'all' && secs.includes(activeSec) ? activeSec : 'all';
          const shown = sel === 'all' ? (docs ?? []) : (grouped.find(([s]) => s === sel)?.[1] ?? []);
          const chip = (key: string, label: string, count: number) => (
            <button key={key} type="button"
              className={`doc-filter-chip${sel === key ? ' active' : ''}`}
              onClick={() => setActiveSec(key)}>
              {label} <span className="doc-filter-count">{count}</span>
            </button>
          );
          return (
            <>
              <div className="doc-filter-bar">
                {chip('all', 'All', docs?.length ?? 0)}
                {grouped.map(([section, items]) => chip(section, section, items.length))}
              </div>
              <div className="doc-list">
                {shown.map(d => (
                  <div className="card doc-item" key={d.name}>
                    <div className="doc-item-head">
                      <svg className="ico"><use href="#i-file" /></svg>
                      <h4>{d.title}</h4>
                      <span className="doc-item-size">{(d.size / 1024).toFixed(0)} KB</span>
                    </div>
                    <p className="doc-item-desc">{d.description}</p>
                    <div className="doc-item-actions">
                      <button className="btn btn-sm btn-primary" onClick={() => openDoc(d)} disabled={loadingDoc === d.name}>
                        {loadingDoc === d.name ? 'Opening…' : 'Open'}
                      </button>
                      <button className="btn btn-sm" onClick={() => downloadDoc(d)}>
                        Download <svg className="ico ico-sm"><use href="#i-external" /></svg>
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </>
          );
        })()}
      </div>

      {/* Operator tools */}
      <div className="page-section">
        <div className="ps-hdr">
          <div>
            <h3 className="ps-title"><svg className="ico"><use href="#i-tools" /></svg> Operator tools</h3>
            <p className="ps-sub">External consoles, opened with your existing Keycloak session. No second login.</p>
          </div>
          <div className="ps-meta">realm · <strong>{REALM}</strong></div>
        </div>
        <div className="doc-tools">
          {TOOLS.filter(t => !t.roles || t.roles.some(r => currentRoles().includes(r))).map(t => (
            <a className="card doc-tool" key={t.name} href={t.url} target="_blank" rel="noreferrer">
              <span className="doc-tool-ico">{t.brand
                ? <img src={t.brand} alt="" style={{ width: 26, height: 26, objectFit: 'contain' }} />
                : <svg className="ico"><use href={`#${t.ico}`} /></svg>}</span>
              <span className="doc-tool-txt">
                <span className="doc-tool-name">{t.name} <svg className="ico ico-sm"><use href="#i-external" /></svg></span>
                <span className="doc-tool-desc">{t.desc}</span>
                <span className="doc-tool-auth muted">{t.auth}</span>
              </span>
            </a>
          ))}
        </div>
      </div>

      {/* Doc viewer modal */}
      {viewer && (
        <div className="doc-modal-backdrop" onClick={() => setViewer(null)}>
          <div className="doc-modal" onClick={ev => ev.stopPropagation()}>
            <div className="doc-modal-hdr">
              <h3>{viewer.title}</h3>
              <div className="doc-modal-actions">
                <button className="btn btn-sm" onClick={() => downloadDoc({ name: viewer.name } as DocMeta)}>Download</button>
                <button className="btn btn-sm" onClick={() => setViewer(null)}>Close</button>
              </div>
            </div>
            <div className="doc-modal-body" dangerouslySetInnerHTML={{ __html: viewer.html }} />
          </div>
        </div>
      )}
    </section>
  );
}

/* widget-module registration (admin catalog runtime)
   1:1; page admin/documentation has exactly this widget. */
import type { AdminWidget } from '../../runtime/types';
const widget: AdminWidget = {
  id: 'adm-documentation',
  Component: Documentation,
};
export default widget;
