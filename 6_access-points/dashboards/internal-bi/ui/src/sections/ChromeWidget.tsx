/**
 * ChromeWidget: mounts a CHROME widget (a widget that lives in a shell slot,
 * not a page grid) for the ACTIVE page. The global filter bar is the first such
 * widget: it's a real registry widget, but it renders once in the pageframe and
 * reads the active page's `global_filters` from the spec.
 *
 * Re-mounts when the active page or the spec changes (so a per-page filter set
 * and per-role scoping both take effect); the widget itself handles filter-value
 * changes via the bus, so we do not re-mount on every filter tweak.
 */
import { useEffect, useRef } from 'react';
import { useBiApp } from '../useBiApp';
import { getWidgetDef } from '../runtime/registry';
import { mountWidget } from '../runtime/PageRenderer';
import type { ResolvedDashboard, ResolvedPage } from '../api/spec';

export default function ChromeWidget({ id }: { id: string }) {
  const { app, state } = useBiApp();
  const ref = useRef<HTMLDivElement>(null);
  const spec: ResolvedDashboard | null = app._spec || null;

  useEffect(() => {
    const el = ref.current;
    if (!el || !getWidgetDef(id)) return;
    const page: ResolvedPage | undefined = spec
      ? (spec.pages || []).find(p => p.id === `internal-bi/${state.active}` || p.id === state.active)
      : undefined;
    if (!page) { el.innerHTML = ''; return; }
    // Synthetic placement: chrome widgets have no grid card / data_url.
    const widget = { id, title: '', kind: 'chrome', requires: null, local_filters: [], data_url: '' };
    return mountWidget(widget as any, undefined, page, el);
  }, [id, spec, state.active]);

  return <div ref={ref} className="chrome-widget" data-chrome-widget={id} />;
}
