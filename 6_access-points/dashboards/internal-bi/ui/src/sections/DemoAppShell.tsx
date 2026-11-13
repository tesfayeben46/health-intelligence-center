/**
 * DemoAppShell: dev-only harness for the app-shell runtime.
 *
 * Reachable only at `#appshell-demo` (App.tsx gates it behind that hash), so it
 * is inert on every live page. It builds a minimal synthetic ResolvedPage with a
 * single section holding the demo-appshell-list widget, and mounts it through
 * `renderAppShell` (not renderPage) so master-detail + modal actually resolve.
 * This is the working reference the workshop restructure follows.
 */
import { useEffect, useRef } from 'react';
import { renderAppShell } from '../runtime/AppShell';
import type { ResolvedPage } from '../api/spec';

const DEMO_PAGE: ResolvedPage = {
  id: 'internal-bi/appshell-demo',
  title: 'App-shell demo',
  ordering: 0,
  global_filters: [],
  widgets: [],
  sections: [{
    id: 'appshell-demo/main',
    title: 'App-shell runtime demo',
    eyebrow: 'Developer harness',
    description: 'Master-detail + modal host + entity seam, on real /gw/marts data.',
    ordering: 0,
    columns: 12,
    dense: true,
    widgets: [{
      id: 'demo-appshell-list',
      title: 'Marts (entity list)',
      kind: 'table',
      requires: null,
      local_filters: [],
      data_url: '',
      col_span: 12,
    }],
  }],
};

export default function DemoAppShell() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const mount = ref.current;
    if (!mount) return;
    return renderAppShell(DEMO_PAGE, mount);
  }, []);
  return <div ref={ref} className="catalog-page" data-demo="appshell" />;
}
