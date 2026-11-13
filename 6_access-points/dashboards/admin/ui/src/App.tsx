/**
 * Admin dashboard · root component.
 *
 * Thin shell: auth gate, furniture (Sidebar / Topbar / Toast / AuthSplash) and
 * <PageRenderer/>. There are no direct section imports: every page surface is a
 * self-contained widget module under src/widgets/<catalog-id>/, discovered by
 * runtime/registry and composed from the caller's resolved catalog spec by
 * runtime/PageRenderer.
 *
 * renderActive() runs exactly once per page change, from setActive's microtask
 * (see useAdminApp). Do not add a useEffect that also calls it: table-style
 * widgets initialise their state on the first run.
 */
import Sidebar from './sections/Sidebar';
import Topbar from './sections/Topbar';
import Toast from './sections/Toast';
import AuthSplash from './sections/AuthSplash';
import PageRenderer from './runtime/PageRenderer';
import type { AdminHost } from './runtime/types';
import { useAuth } from './api/useAuth';
import { useAdminApp, useBootAdminApp } from './useAdminApp';

export default function App() {
  useBootAdminApp();
  const { state, setActive } = useAdminApp();
  const { user } = useAuth();
  const host: AdminHost = { active: state.active, setActive };
  return (
    <>
      {/* Auth gate: cover the app until the session is confirmed so protected
          content never flashes before useAuth redirects to Keycloak. */}
      {!user?.authenticated && <AuthSplash />}
      <Toast />
      <div className="admin-shell">
        <Sidebar />
        <main className="admin-main">
          <Topbar />
          <div className="admin-pageframe">
            <PageRenderer host={host} />
          </div>
        </main>
      </div>
    </>
  );
}
