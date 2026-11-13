/**
 * Dashboard Control: the operator cockpit over the reconciled catalog.
 *
 * One flat, searchable and filterable table of every widget placement across
 * all dashboards, with enable/disable toggles per row. Toggles write
 * ops_perm.catalog_override via the gateway (runtime state that survives every
 * re-seed):
 *   · Enabled (widget_enabled): disable this widget everywhere.
 *   · Placement (placement_enabled): disable it on this page only.
 * Toggles only subtract visibility: grants still gate first, so nothing here
 * can widen access. Below the table: the soft-orphan panel (widgets whose
 * code left the authored catalog; grants are kept until an explicit purge)
 * and the read-only widget and filter catalogs every placement draws from.
 *
 * The authored catalog itself is edited as data files in the repo
 * (<dash>/catalog/*.yaml and widget.yaml next to each widget) and lands via
 * seed-app-security.sh; this page controls the runtime layer on top.
 */
import { useEffect, useMemo, useState } from 'react';
import * as api from '../../api/permAdmin';
import { useTableKit, TableToolbar, TablePager, SortTh } from '../../kit/tableKit';

function Toggle({ on, busy, onClick, title }:
                { on: boolean; busy: boolean; onClick: () => void; title: string }) {
  return (
    <button className={`btn btn-sm ${on ? 'btn-ghost' : 'btn-danger'}`} disabled={busy}
            title={title} onClick={onClick} data-on={on ? '1' : '0'}>
      {on ? 'Enabled' : 'Disabled'}
    </button>
  );
}

/* flatten the catalog tree into one row per widget PLACEMENT */
type PlacementRow = {
  key: string;                 // unique per placement (React key)
  widget_id: string;
  widget_title: string;
  kind: string;
  dashboard_id: string;
  dashboard_title: string;
  page_id: string;
  page_title: string;
  section_title: string;
  orphaned: boolean;
  widget_enabled: boolean;
  placement_enabled: boolean;
};

function flattenTree(tree: api.CtDashboard[]): PlacementRow[] {
  const rows: PlacementRow[] = [];
  let i = 0;
  for (const d of tree) {
    for (const p of d.pages) {
      const emit = (w: api.CtWidget, section_title: string) => {
        rows.push({
          key: `${p.id}::${w.id}::${i++}`,
          widget_id: w.id, widget_title: w.title, kind: w.kind,
          dashboard_id: d.id, dashboard_title: d.title,
          page_id: p.id, page_title: p.title, section_title,
          orphaned: w.orphaned,
          widget_enabled: w.widget_enabled,
          placement_enabled: w.placement_enabled,
        });
      };
      for (const s of p.sections) for (const w of s.widgets) emit(w, s.title || s.id);
      for (const w of p.default_widgets) emit(w, '');
    }
  }
  return rows;
}

function DashboardControl() {
  const [tree, setTree] = useState<api.CtDashboard[]>([]);
  const [orphans, setOrphans] = useState<api.CatalogOrphan[]>([]);
  const [catalog, setCatalog] = useState<api.Catalog | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [dashFilter, setDashFilter] = useState<string>('all');

  async function refresh() {
    try {
      // one fetch triple: the placement tree, the orphan list, and the
      // authored catalog (feeds the two Catalog panels below).
      const [t, o, c] = await Promise.all([
        api.getCatalogTree(), api.listCatalogOrphans(), api.getCatalog(),
      ]);
      setTree(t.dashboards); setOrphans(o); setCatalog(c); setErr('');
    } catch (e: any) { setErr(String(e?.message || e)); }
  }
  useEffect(() => { void refresh(); }, []);

  async function toggle(kind: api.CatalogOverrideIn['kind'], scope_id: string, enabled: boolean) {
    setBusy(true);
    try {
      if (enabled) {
        // re-enabling = remove the disable override entirely (keeps the table lean)
        await api.deleteCatalogOverride(kind, scope_id);
      } else {
        await api.putCatalogOverride({ kind, scope_id, enabled: false });
      }
      await refresh();
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(false); }
  }

  async function purge(id: string) {
    if (!window.confirm(`Purge orphaned widget "${id}"? Its preserved grants and placements are deleted permanently.`)) return;
    setBusy(true);
    try { await api.purgeCatalogOrphan(id); await refresh(); }
    catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(false); }
  }

  const allRows = useMemo(() => flattenTree(tree), [tree]);
  const rows = useMemo(
    () => (dashFilter === 'all' ? allRows : allRows.filter(r => r.dashboard_id === dashFilter)),
    [allRows, dashFilter]);

  const kit = useTableKit(rows, {
    pageSize: 15,
    search: r => `${r.widget_id} ${r.widget_title} ${r.dashboard_id} ${r.dashboard_title} ${r.page_id} ${r.page_title}`,
    sortVals: {
      widget: r => r.widget_id,
      dashboard: r => r.dashboard_title,
      page: r => r.page_title,
      kind: r => r.kind,
    },
    initialSort: { key: 'dashboard', dir: 'asc' },
  });

  return (
    <section id="dashboard-control" data-testid="dashboard-control">
      <p className="page-lede">
        Runtime enable/disable over the reconciled catalog, surviving every re-seed. Toggles
        <strong> subtract visibility only</strong>: role grants still gate first, nothing here
        widens access. The authored structure itself is edited as catalog data files in the repo.
      </p>

      {err && (
        <div className="alert-banner" role="alert"
             style={{ background: 'var(--alert-50)', color: 'var(--alert)' }}>{err}</div>
      )}

      {/* Widget placements (flat, searchable, filterable) */}
      <div className="page-section">
        <div className="ps-hdr">
          <div>
            <h3 className="ps-title"><svg className="ico"><use href="#i-tools" /></svg> Widget placements</h3>
            <p className="ps-sub">
              Every widget placement across all dashboards. <strong>Enabled</strong> disables the
              widget everywhere; <strong>Placement</strong> disables it on this page only.
            </p>
          </div>
          <div className="ps-meta">
            <button className="btn btn-sm btn-ghost" onClick={() => void refresh()} disabled={busy}>
              <svg className="ico ico-sm"><use href="#i-refresh" /></svg> Refresh
            </button>
          </div>
        </div>

        <div className="card card-elev" style={{ paddingTop: '0.8rem' }} data-testid="placement-table">
          <TableToolbar kit={kit} placeholder="Search widget, dashboard, page…" unit="placements">
            <select value={dashFilter} data-testid="dash-filter" onChange={e => setDashFilter(e.target.value)}>
              <option value="all">All dashboards</option>
              {(catalog?.dashboards ?? []).map(d => <option key={d.id} value={d.id}>{d.title}</option>)}
            </select>
          </TableToolbar>
          <div className="tk-scroll">
            <table className="dash">
              <thead><tr>
                <SortTh kit={kit} k="widget">Widget</SortTh>
                <SortTh kit={kit} k="dashboard">Dashboard</SortTh>
                <SortTh kit={kit} k="page">Page</SortTh>
                <SortTh kit={kit} k="kind">Kind</SortTh>
                <th>Enabled</th>
                <th>Placement</th>
              </tr></thead>
              <tbody>
                {kit.pageRows.map(r => (
                  <tr key={r.key} data-testid={`ct-w-${r.page_id}-${r.widget_id}`}>
                    <td>
                      <code>{r.widget_id}</code>
                      {r.orphaned && <span className="pill watch" style={{ marginLeft: '0.35rem' }}>orphaned</span>}
                      <br /><span className="text-xs muted">{r.widget_title}</span>
                    </td>
                    <td>{r.dashboard_title}</td>
                    <td>{r.page_title}{r.section_title && <span className="text-xs muted"> · {r.section_title}</span>}</td>
                    <td><span className="pill neutral">{r.kind}</span></td>
                    <td>
                      <Toggle on={r.widget_enabled} busy={busy} title="Enable/disable this widget EVERYWHERE"
                              onClick={() => void toggle('widget', r.widget_id, !r.widget_enabled)} />
                    </td>
                    <td>
                      <Toggle on={r.placement_enabled} busy={busy} title="Enable/disable only on this page"
                              onClick={() => void toggle('placement', `${r.page_id}::${r.widget_id}`, !r.placement_enabled)} />
                    </td>
                  </tr>
                ))}
                {tree.length > 0 && rows.length === 0 && (
                  <tr><td colSpan={6} className="muted" style={{ padding: '1rem', textAlign: 'center' }}>No placements match the current filters.</td></tr>
                )}
                {tree.length === 0 && !err && (
                  <tr><td colSpan={6} className="muted" style={{ padding: '1rem' }}>Loading placements…</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <TablePager kit={kit} />
        </div>
      </div>

      {/* Orphaned widgets (kept below the table) */}
      {orphans.length > 0 && (
        <div className="page-section" data-testid="orphan-panel">
          <div className="ps-hdr">
            <div>
              <h3 className="ps-title"><svg className="ico"><use href="#i-alert" /></svg> Orphaned widgets <span className="text-xs muted">({orphans.length})</span></h3>
              <p className="ps-sub">Removed from the authored catalog; hidden everywhere. Grants/placements are
                preserved, and re-adding the widget revives them. Purge to delete permanently.</p>
            </div>
          </div>
          <div className="card card-elev" style={{ paddingTop: '0.8rem' }}>
            <table className="dash">
              <thead><tr>
                <th>Widget</th><th>Orphaned</th>
                <th style={{ textAlign: 'right' }}>Grants</th>
                <th style={{ textAlign: 'right' }}>Placements</th>
                <th />
              </tr></thead>
              <tbody>{orphans.map(o => (
                <tr key={o.id}>
                  <td><code>{o.id}</code> <span className="muted">{o.title}</span></td>
                  <td>{new Date(o.orphaned_at).toLocaleString()}</td>
                  <td style={{ textAlign: 'right' }}>{o.grant_count}</td>
                  <td style={{ textAlign: 'right' }}>{o.placement_count}</td>
                  <td style={{ textAlign: 'right' }}><button className="btn btn-sm btn-danger" disabled={busy}
                              onClick={() => void purge(o.id)}>Purge</button></td>
                </tr>))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Catalogs */}
      <div className="page-section">
        <div className="ps-hdr">
          <div>
            <h3 className="ps-title"><svg className="ico"><use href="#i-grid" /></svg> Catalogs</h3>
            <p className="ps-sub">What is renderable and grantable: the authored widget &amp; filter catalogs behind every placement above.</p>
          </div>
        </div>
        <WidgetCatalogPanel catalog={catalog} />
        <FilterCatalogPanel catalog={catalog} />
      </div>
    </section>
  );
}

/* Catalogs (read-only)
   Widgets share no modules, so these live in full alongside the placements
   they back. */

function WidgetCatalogPanel({ catalog }: { catalog: api.Catalog | null }) {
  const [kindFilter, setKindFilter] = useState<string>('all');
  const [dashFilter, setDashFilter] = useState<string>('all');

  // For each widget, the dashboard it belongs to. There is no widget-to-page
  // mapping in the catalog payload (many-to-many, resolved at grant time),
  // so use the id-prefix convention every widget follows.
  const widgetDashboards = useMemo(() => {
    const out: Record<string, Set<string>> = {};
    if (!catalog) return out;
    const mapping: Record<string, string[]> = {
      pub: ['public'], ov: ['internal-bi'], dz: ['internal-bi'],
      rm: ['internal-bi'], sv: ['internal-bi'], ex: ['internal-bi'],
      st: ['internal-bi'], adm: ['admin'], ws: ['workshop'],
    };
    catalog.widgets.forEach(w => {
      const prefix = w.id.split('-')[0];
      out[w.id] = new Set(mapping[prefix] || []);
    });
    return out;
  }, [catalog]);

  const kinds = catalog ? Array.from(new Set(catalog.widgets.map(w => w.kind))).sort() : [];

  const rows = useMemo(() => (catalog?.widgets ?? []).filter(w => {
    if (kindFilter !== 'all' && w.kind !== kindFilter) return false;
    if (dashFilter !== 'all' && !widgetDashboards[w.id]?.has(dashFilter)) return false;
    return true;
  }), [catalog, kindFilter, dashFilter, widgetDashboards]);

  const kit = useTableKit(rows, {
    pageSize: 12,
    search: w => `${w.id} ${w.title}`,
    sortVals: { id: w => w.id, title: w => w.title, kind: w => w.kind },
    initialSort: { key: 'id', dir: 'asc' },
  });

  return (
    <div className="card card-elev" style={{ paddingTop: '0.8rem' }}>
      <div className="card-hdr" style={{ paddingBottom: '0.4rem' }}>
        <h4>Widget catalog</h4>
        <span className="meta">every renderable widget in ops_perm.widget · read-only</span>
      </div>
      <TableToolbar kit={kit} placeholder="Search widget id or title…" unit="widgets">
        <select value={dashFilter} onChange={(e) => setDashFilter(e.target.value)}>
          <option value="all">All dashboards</option>
          {(catalog?.dashboards ?? []).map(d => <option key={d.id} value={d.id}>{d.title}</option>)}
        </select>
        <select value={kindFilter} onChange={(e) => setKindFilter(e.target.value)}>
          <option value="all">All kinds</option>
          {kinds.map(k => <option key={k} value={k}>{k}</option>)}
        </select>
      </TableToolbar>
      <div className="tk-scroll">
        <table className="dash">
          <thead>
            <tr>
              <SortTh kit={kit} k="id">ID</SortTh>
              <SortTh kit={kit} k="title">Title</SortTh>
              <SortTh kit={kit} k="kind">Kind</SortTh>
              <th>Dashboard</th><th>Mart-backed?</th>
            </tr>
          </thead>
          <tbody>
            {kit.pageRows.map(w => {
              const dashes = Array.from(widgetDashboards[w.id] || []);
              return (
                <tr key={w.id}>
                  <td><code>{w.id}</code></td>
                  <td>{w.title}</td>
                  <td><span className="pill neutral">{w.kind}</span></td>
                  <td>{dashes.length ? dashes.join(', ') : <span className="muted">n/a</span>}</td>
                  <td>
                    {w.requires
                      ? <span className="pill info">{`${w.requires.subject}/${w.requires.granularity}`}</span>
                      : <span className="muted">no</span>}
                  </td>
                </tr>
              );
            })}
            {catalog && rows.length === 0 && (
              <tr><td colSpan={5} className="muted" style={{ padding: '1rem' }}>No widgets match the current filters.</td></tr>
            )}
            {!catalog && <tr><td colSpan={5} className="muted" style={{ padding: '1rem' }}>Loading catalog…</td></tr>}
          </tbody>
        </table>
      </div>
      <TablePager kit={kit} />
    </div>
  );
}

function FilterCatalogPanel({ catalog }: { catalog: api.Catalog | null }) {
  const kit = useTableKit(catalog?.filters ?? null, {
    pageSize: 12,
    search: f => `${f.id} ${f.title} ${f.source} ${f.source_ref ?? ''}`,
    sortVals: { id: f => f.id, title: f => f.title, source: f => f.source },
    initialSort: { key: 'id', dir: 'asc' },
  });

  return (
    <div className="card card-elev mt-md" style={{ paddingTop: '0.8rem' }}>
      <div className="card-hdr" style={{ paddingBottom: '0.4rem' }}>
        <h4>Filter catalog</h4>
        <span className="meta">the dimensions a role can be constrained by · read-only</span>
      </div>
      <p className="text-xs muted" style={{ marginBottom: '0.5rem' }}>
        e.g. "RHB-Oromia can only see <code>region=Oromia</code>". The <code>source</code> enum
        tells the resolver where canonical values come from: static enum, live mart, or ops-DB lookup.
      </p>
      <TableToolbar kit={kit} placeholder="Search filter id, title, source…" unit="filters" />
      <div className="tk-scroll">
        <table className="dash">
          <thead>
            <tr>
              <SortTh kit={kit} k="id">ID</SortTh>
              <SortTh kit={kit} k="title">Title</SortTh>
              <SortTh kit={kit} k="source">Source</SortTh>
              <th>Source ref</th><th>Canonical values</th>
            </tr>
          </thead>
          <tbody>
            {kit.pageRows.map(f => (
              <tr key={f.id}>
                <td><code>{f.id}</code></td>
                <td>{f.title}</td>
                <td><span className={`pill ${f.source === 'static' ? 'good' : 'info'}`}>{f.source}</span></td>
                <td>{f.source_ref ? <code>{f.source_ref}</code> : <span className="muted">n/a</span>}</td>
                <td>
                  {f.source === 'static' && f.static_values
                    ? <span className="text-xs">{f.static_values.length} values: {f.static_values.slice(0, 5).join(', ')}{f.static_values.length > 5 ? '…' : ''}</span>
                    : <span className="muted text-xs">(resolved at request time)</span>}
                </td>
              </tr>
            ))}
            {catalog && catalog.filters.length === 0 && (
              <tr><td colSpan={5} className="muted" style={{ padding: '1rem' }}>No filters defined.</td></tr>
            )}
            {!catalog && <tr><td colSpan={5} className="muted" style={{ padding: '1rem' }}>Loading catalog…</td></tr>}
          </tbody>
        </table>
      </div>
      <TablePager kit={kit} />
    </div>
  );
}

import type { AdminWidget } from '../../runtime/types';
const widget: AdminWidget = {
  id: 'adm-dashboard-control',
  Component: DashboardControl,
};
export default widget;
