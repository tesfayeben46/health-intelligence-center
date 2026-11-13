/**
 * Internal-BI dashboard · root component.
 *
 * React state for the top-level UI (active page, dropdown open, modal flag,
 * filters) over the biApp.ts shell state; pages are catalog-driven
 * (CatalogPage to runtime/PageRenderer).
 */
import Sidebar from './sections/Sidebar';
import Topbar from './sections/Topbar';
import ChromeWidget from './sections/ChromeWidget';
import CatalogPage from './sections/CatalogPage';
import DemoAppShell from './sections/DemoAppShell';
import Predictions from './sections/Predictions';
import AlertModal from './modals/AlertModal';
import NotificationPanel from './modals/NotificationPanel';
import ChatbotPanel from './sections/ChatbotPanel';
import AuthSplash from './sections/AuthSplash';
import { useAuth } from './api/useAuth';
import { useBiApp, useBootBiApp } from './useBiApp';

export default function App() {
  useBootBiApp();
  const { app, state } = useBiApp();
  const { user } = useAuth();
  // Authorization gate: a page absent from the resolved permission spec
  // must not render at all (its JSX shell carries synthetic content).
  // biApp.renderActive() applies the same check before drawing charts, and
  // loadSpec() hops off a non-granted active page once the spec resolves.
  const granted = (id: string) => app.pageGranted?.(id) ?? true;

  // Dev-only: `#appshell-demo` swaps the page area for the app-shell reference
  // harness (master-detail + modal + entity seam). Inert on every normal visit
  // (no such hash) so live pages render unchanged.
  const isDemo = typeof window !== 'undefined' && window.location.hash === '#appshell-demo';

  // renderActive() is fired by setActive/setFilter directly (see useBiApp.ts).
  // Do not add a useEffect here that calls it: the paginated_table widget
  // renderer sets ws.sort after the first call, so a second render would
  // re-sort the rows.

  return (
    <>
      {/* Auth gate: cover the app until the session is confirmed so protected
          content never flashes before useAuth redirects to Keycloak. */}
      {!user?.authenticated && <AuthSplash />}
      <div className="bi-shell">
        <Sidebar />
        <main className="bi-main">
          <Topbar />
          <div className="bi-pageframe">
            {/* The global filter is a chrome WIDGET (registry: chrome/global-filter),
                not shell furniture: it reads the active page's scoped global_filters.
                It renders on every catalog page, Overview included; the widget
                collapses on any page that resolves zero filters (e.g. Export). */}
            <ChromeWidget id="chrome/global-filter" />
            <div className="bi-pages">
              {isDemo && <DemoAppShell />}
              {!isDemo && <>
                {state.active === 'overview' && granted('overview') && <CatalogPage pageId="overview" />}
                {state.active === 'disease'  && granted('disease')  && <CatalogPage pageId="disease" />}
                {state.active === 'resource' && granted('resource') && <CatalogPage pageId="resource" />}
                {state.active === 'control-tower' && granted('control-tower') && <CatalogPage pageId="control-tower" />}
                {state.active === 'service'  && granted('service')  && <CatalogPage pageId="service" />}
                {state.active === 'predictions' && granted('predictions') && <Predictions />}
                {state.active === 'export'   && granted('export')   && <CatalogPage pageId="export" />}
                {state.active === 'settings' && granted('settings') && <CatalogPage pageId="settings" />}
              </>}
            </div>
          </div>
        </main>
      </div>

      {state.notifPanel && <NotificationPanel />}
      {state.alertModal && <AlertModal />}
      <ChatbotPanel />
    </>
  );
}
