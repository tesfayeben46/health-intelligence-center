/**
 * Workshop dashboard root component (catalog-driven shell).
 *
 * After auth, the resolved dashboard spec is fetched from the gateway
 * (GET /gw/permissions/dashboard/workshop/spec). The sidebar nav is drawn
 * from spec.pages (sorted by ordering, Settings pinned to the bottom) and every
 * page renders through CatalogPage to renderAppShell: sections and widget grid
 * from the catalog, plus the master-detail and modal layers. No page content is
 * hardcoded; the spec only contains what the role is granted, so nav and grids
 * follow permissions.
 */
import { useEffect, useState } from 'react';
import Topbar from './sections/Topbar';
import CatalogPage from './sections/CatalogPage';
import Footer from './sections/Footer';
import AuthSplash from './sections/AuthSplash';
import { useAuth } from './api/useAuth';
import { useDashboardSpec, type ResolvedPage } from './api/spec';
import { host, registerHostNavigation } from './runtime/host';
import { useProfile, logoUrl } from './hooks/useProfile';

const SETTINGS_PAGE = 'workshop/settings';

/** Shell chrome only: page content comes from the catalog; this map picks a
 *  sprite icon per known page id (default: grid). */
const PAGE_ICONS: Record<string, string> = {
  'workshop/workspace': 'i-cpu',
  'workshop/datasets': 'i-database',
  'workshop/models': 'i-pulse',
  'workshop/projects': 'i-microscope',
  'workshop/help': 'i-book',
  'workshop/settings': 'i-settings',
};

export default function App() {
  const { user } = useAuth();
  const profile = useProfile();
  const { spec, loaded, denied } = useDashboardSpec('workshop');
  const [active, setActive] = useState<string | null>(null);

  const pages: ResolvedPage[] = [...(spec?.pages ?? [])].sort((a, b) => a.ordering - b.ordering);
  const mainPages = pages.filter(p => p.id !== SETTINGS_PAGE);
  const settingsPage = pages.find(p => p.id === SETTINGS_PAGE) || null;
  const activeId = active && pages.some(p => p.id === active) ? active : (pages[0]?.id ?? null);
  const activePage = pages.find(p => p.id === activeId) || null;

  // Navigate: close any open detail/modal so it never follows across pages.
  const goto = (pageId: string) => {
    host.closeModal();
    host.closeDetail();
    setActive(pageId);
  };

  // Widgets navigate through host.navigateToPage: wire it to this shell.
  useEffect(() => {
    registerHostNavigation((pageId) => {
      const full = pageId.startsWith('workshop/') ? pageId : `workshop/${pageId}`;
      goto(full);
    });
  }, []);

  const navLink = (p: ResolvedPage) => (
    <a
      key={p.id}
      className={p.id === activeId ? 'active' : undefined}
      onClick={() => goto(p.id)}
    >
      <svg className="ico"><use href={`#${PAGE_ICONS[p.id] || 'i-grid'}`} /></svg> {p.title}
    </a>
  );

  return (
    <>
      {/* Auth gate: cover the app until the session is confirmed so protected
          content never flashes before useAuth redirects to Keycloak. */}
      {!user?.authenticated && <AuthSplash />}
      <div className="ws-shell">
        <aside className="ws-side">
          <div className="ws-sidehead">
            <a href="/" title="Back to public site">
              {logoUrl(profile)
                ? <img className="sidehead-logo" src={logoUrl(profile)!} alt="" />
                : null}
              <span className="sidehead-text">
                <span className="title">{profile.platform?.abbr || 'NHDW'}</span>
                <span className="subtitle">Workshop portal</span>
              </span>
            </a>
          </div>

          {/* Nav straight from the resolved spec: pages absent from the
              role's grant simply don't exist here. */}
          {mainPages.length > 0 && <h6>Workshop</h6>}
          {mainPages.map(navLink)}
          {loaded && !denied && spec && mainPages.length === 0 && !settingsPage && (
            <p style={{ padding: '0 1rem', fontSize: '0.78rem', opacity: 0.7 }}>
              No pages are granted to your role on this dashboard.
            </p>
          )}

          <span className="ws-side-spacer"></span>
          {/* Settings pinned to the bottom, separated from the main list. */}
          {settingsPage && (
            <>
              <h6>Account</h6>
              {navLink(settingsPage)}
            </>
          )}
          <a href="/">
            <svg className="ico"><use href="#i-arrow-right" /></svg> Public site
          </a>
        </aside>

        <main className="ws-main">
          <Topbar pageTitle={activePage?.title || 'Workshop'} />

          <div className="ws-pageframe">
            {denied && (
              <div className="widget-empty" style={{ padding: '3rem' }}>
                This dashboard isn't granted to your role.
              </div>
            )}
            {!denied && loaded && !spec && (
              <div className="widget-empty" style={{ padding: '3rem' }}>
                The dashboard catalog could not be loaded from the gateway.
                Try reloading once the gateway is reachable.
              </div>
            )}
            {!denied && activeId && (
              <CatalogPage spec={spec} pageId={activeId} />
            )}
            {!denied && !loaded && !activeId && (
              <div className="widget-empty" style={{ padding: '3rem' }}>Loading…</div>
            )}
          </div>

          <Footer />
        </main>
      </div>
    </>
  );
}
