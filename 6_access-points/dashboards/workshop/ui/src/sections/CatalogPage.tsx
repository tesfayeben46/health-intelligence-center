/**
 * CatalogPage: the catalog-driven page host. Given a pageId, it finds the
 * per-role ResolvedPage in the loaded gateway spec and hands it to the runtime's
 * renderAppShell, which draws the sections and reflowing widget grid from the
 * catalog and hosts the master-detail and modal layers (project-create,
 * dataset-detail, compute-request, project-submit). Adding a page, section or
 * widget is a catalog and widget-folder change.
 */
import { useEffect, useRef } from 'react';
import { renderAppShell } from '../runtime/AppShell';
import type { ResolvedDashboard, ResolvedPage } from '../api/spec';

export default function CatalogPage({
  spec, pageId,
}: {
  spec: ResolvedDashboard | null;
  pageId: string;
}) {
  const ref = useRef<HTMLDivElement>(null);

  // Render the page from the spec. Re-runs when the spec resolves
  // or the pageId changes. Per-widget selection reactivity (ws_model /
  // ws_project) is handled by the filter bus, not by re-rendering the page.
  useEffect(() => {
    const mount = ref.current;
    if (!mount) return;
    if (!spec) {
      mount.innerHTML = '<div class="widget-empty" style="padding:3rem">Loading…</div>';
      return;
    }
    const page = (spec.pages || []).find(
      (p: ResolvedPage) => p.id === `workshop/${pageId}` || p.id === pageId,
    );
    if (!page) {
      mount.innerHTML = '<div class="widget-empty" style="padding:3rem">This page isn\'t granted to your role.</div>';
      return;
    }
    return renderAppShell(page, mount);
  }, [spec, pageId]);

  return <div ref={ref} className="catalog-page" />;
}
