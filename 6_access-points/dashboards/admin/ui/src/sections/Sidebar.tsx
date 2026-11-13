/**
 * Admin sidebar. The nav mirrors the ops_perm page catalog for dashboard
 * `admin`, in catalog order:
 *   Infrastructure       Health & topology · Dashboard Control · Alert routing
 *   Data                 Quality & lineage · MDM Stewardship · Column Governance ·
 *                        Models and datasets
 *   Identity and Access  Users and Access · Roles and Permissions · SSO & identity
 *   (bottom)             Documentation
 * Operator tools are deep-linked where they are relevant (topology to Headlamp,
 * Quality & lineage to dbt Docs / object store / Airflow, Models to MLflow), with
 * the full reference strip on the Documentation page.
 *
 * Every item is shown only when the caller's resolved dashboard spec grants
 * that page's widget; grants come from ops_perm role_widget rows, never a role
 * list here. Labels of the catalog-inserted items come from the catalog page
 * titles.
 */
import { useAdminApp } from '../useAdminApp';
import { useDashboardSpec, specAllowsWidget, specPageTitle } from '../api/spec';
import { useProfile, logoUrl } from '../hooks/useProfile';

// `widget` is the catalog widget the page carries; a nav item is shown only
// when the caller's resolved spec grants it. Without that gate a role with a
// narrow admin grant (a steward, whose admin home is stewardship and column
// governance) would see the full operator nav and land on empty pages.
type NavItem = { id: string; ico: string; title: string; widget: string; badge?: boolean };

const ITEMS: Array<{ group: string; items: NavItem[] }> = [
  { group: 'Infrastructure', items: [
    { id: 'infra',   ico: 'i-tools',         title: 'Health & topology', widget: 'adm-topology' },
    { id: 'alerts',  ico: 'i-bell',          title: 'Alert routing',     widget: 'adm-alerts-routing' },
  ]},
  { group: 'Data', items: [
    { id: 'dq',      ico: 'i-shield-check',  title: 'Quality & lineage', widget: 'adm-dq' },
  ]},
  { group: 'Identity and Access', items: [
    { id: 'users-access',      ico: 'i-users',    title: 'Users and Access',      widget: 'adm-users-access', badge: true },
    { id: 'roles-permissions', ico: 'i-policy',   title: 'Roles and Permissions', widget: 'adm-roles-permissions' },
    { id: 'sso',               ico: 'i-lock',     title: 'SSO & identity',        widget: 'adm-sso-cards' },
  ]},
];

/** Insert `item` into `items` right after the entry with id `afterId`
 *  (append if the anchor is somehow missing). */
function insertAfter(items: NavItem[], afterId: string, item: NavItem): NavItem[] {
  const i = items.findIndex(it => it.id === afterId);
  if (i === -1) return [...items, item];
  return [...items.slice(0, i + 1), item, ...items.slice(i + 1)];
}

export default function Sidebar() {
  const profile = useProfile();
  const { state, setActive } = useAdminApp();
  // Resolved permission catalog for this caller: pages arrive with their
  // widget lists already grant-filtered by the gateway.
  const { spec } = useDashboardSpec('admin');

  // Build the nav: static items + catalog-gated additions in their
  // catalog-ordained slots.
  const groups = ITEMS.map(g => {
    // Gate the static items on the caller's grants. While the spec is still
    // loading or unusable (network/5xx/denied) show them all; each page then
    // degrades on its own.
    let items = spec ? g.items.filter(it => specAllowsWidget(spec, `admin/${it.id}`, it.widget))
                     : g.items;
    if (g.group === 'Infrastructure') {
      // Dashboard Control is catalog-gated like models-datasets: shown only
      // when the caller's spec grants the widget (manage_catalog roles).
      if (specAllowsWidget(spec, 'admin/dashboard-control', 'adm-dashboard-control')) {
        items = insertAfter(items, 'infra', {
          id: 'dashboard-control', ico: 'i-tools', widget: 'adm-dashboard-control',
          title: specPageTitle(spec, 'admin/dashboard-control', 'Dashboard Control'),
        });
      }
    }
    if (g.group === 'Data') {
      // "Models and datasets" carries one widget (adm-models-datasets) covering
      // model monitoring and the review queue; show the nav item when that
      // widget is granted. Catalog order: dq (40), this (42).
      if (specAllowsWidget(spec, 'admin/models-datasets', 'adm-models-datasets')) {
        items = insertAfter(items, 'dq', {
          id: 'models-datasets', ico: 'i-pulse', widget: 'adm-models-datasets',
          title: specPageTitle(spec, 'admin/models-datasets', 'Models and datasets'),
        });
      }
      // MDM stewardship, catalog-gated (stewards and ops admins). Inserted
      // after 'dq' last so the final order matches the catalog:
      // dq (40), mdm (41), models-datasets (42).
      if (specAllowsWidget(spec, 'admin/mdm', 'adm-mdm-review')) {
        items = insertAfter(items, 'dq', {
          id: 'mdm', ico: 'i-check-circle', widget: 'adm-mdm-review',
          title: specPageTitle(spec, 'admin/mdm', 'MDM Stewardship'),
        });
      }
      // Column governance. Catalog order: dq (40), mdm (41), this (42),
      // so anchor on 'mdm' when it is present, else on 'dq'.
      if (specAllowsWidget(spec, 'admin/classification', 'adm-classification')) {
        items = insertAfter(items, items.some(i => i.id === 'mdm') ? 'mdm' : 'dq', {
          id: 'classification', ico: 'i-shield', widget: 'adm-classification',
          title: specPageTitle(spec, 'admin/classification', 'Column Governance'),
        });
      }
    }
    return { group: g.group, items };
  });

  // Documentation is pinned to the bottom of the sidebar, separated from the
  // groups, and catalog-gated on adm-documentation.
  const showDocs = specAllowsWidget(spec, 'admin/documentation', 'adm-documentation');
  const docsTitle = specPageTitle(spec, 'admin/documentation', 'Documentation');

  return (
    <aside className="admin-side">
      <div className="admin-sidehead">
        <a href="/" title="Back to public site">
          {logoUrl(profile)
            ? <img className="sidehead-logo" src={logoUrl(profile)!} alt="" />
            : null}
          <span className="sidehead-text">
            <span className="title">{profile.platform?.abbr || 'NHDW'}</span>
            <span className="subtitle">Admin console</span>
          </span>
        </a>
      </div>

      {groups.filter(g => g.items.length > 0).map(g => (
        <div key={g.group}>
          <h6>{g.group}</h6>
          {g.items.map(it => (
            <a key={it.id} className={state.active === it.id ? 'active' : undefined} onClick={() => setActive(it.id)}>
              <svg className="ico"><use href={'#' + it.ico} /></svg> {it.title}
              {it.badge && <span className="badge">{state.pendingRequestCount}</span>}
            </a>
          ))}
        </div>
      ))}

      {showDocs && (
        <div className="admin-side-bottom">
          <a className={state.active === 'documentation' ? 'active' : undefined} onClick={() => setActive('documentation')}>
            <svg className="ico"><use href="#i-book" /></svg> {docsTitle}
          </a>
        </div>
      )}
    </aside>
  );
}
