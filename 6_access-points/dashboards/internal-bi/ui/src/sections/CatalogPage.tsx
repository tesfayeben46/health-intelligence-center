/**
 * CatalogPage: the dynamic, catalog-driven page. Given a pageId, it finds the
 * per-role ResolvedPage in the loaded gateway spec and hands it to the runtime
 * PageRenderer, which draws sections and a reflowing widget grid entirely from the
 * catalog (no hardcoded JSX, no static manifest). This one component replaces a
 * bespoke page component per page: adding a page/section/widget is a catalog +
 * widget-folder change, never a shell edit.
 */
import { useEffect, useRef } from 'react';
import { useBiApp } from '../useBiApp';
import { renderPage } from '../runtime/PageRenderer';
import { emitFilterChange } from '../runtime/filterBus';
import { invalidateWidgetData } from '../runtime/dataHook';
import type { ResolvedDashboard, ResolvedPage } from '../api/spec';

export default function CatalogPage({ pageId }: { pageId: string }) {
  const { app, state } = useBiApp();
  const ref = useRef<HTMLDivElement>(null);
  const spec: ResolvedDashboard | null = app._spec || null;

  // Render the page from the spec. Re-runs when the spec resolves (null to obj)
  // or the pageId changes. Per-widget filter reactivity is handled by the bus,
  // not by re-rendering the whole page.
  useEffect(() => {
    const mount = ref.current;
    if (!mount) return;
    if (!spec) { mount.innerHTML = '<div class="widget-empty" style="padding:3rem">Loading…</div>'; return; }
    const page = (spec.pages || []).find(
      (p: ResolvedPage) => p.id === `internal-bi/${pageId}` || p.id === pageId,
    );
    if (!page) { mount.innerHTML = '<div class="widget-empty" style="padding:3rem">This page isn\'t granted to your role.</div>'; return; }
    return renderPage(page, mount);
  }, [spec, pageId]);

  // Filter change: invalidate cached data + tell reacting widgets to redraw.
  // Skip the initial mount (the page render already drew everything with the
  // current filters: firing here would re-fetch/redraw every widget for nothing).
  const firstFilterRun = useRef(true);
  useEffect(() => {
    if (firstFilterRun.current) { firstFilterRun.current = false; return; }
    invalidateWidgetData();
    emitFilterChange(['geography', 'programme', 'period']);
  }, [state.filters.geography, state.filters.programme, state.filters.period]);

  return <div ref={ref} className="catalog-page" />;
}
