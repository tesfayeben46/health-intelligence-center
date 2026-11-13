/**
 * Admin dashboard · page renderer.
 *
 * Renders the active page's widgets from the caller's resolved catalog spec
 * (the same useDashboardSpec module cache the Sidebar uses, so one network
 * fetch). One catalog widget id maps to one registry module, rendered inside
 * `.admin-pageframe` with no extra wrappers.
 *
 * When the spec is not usable (loading on first paint, network failure, or
 * denied) the page falls back to FALLBACK_PAGE_WIDGETS, the full catalog
 * composition; see pages.ts.
 *
 * An id no module owns renders a visible placeholder rather than a silent gap,
 * matching the public dashboard's WidgetSlot.
 */
import type { AdminHost } from './types';
import { FALLBACK_PAGE_WIDGETS } from './pages';
import { getWidget, registeredIds } from './registry';
import { useDashboardSpec } from '../api/spec';

function MissingWidget({ id }: { id: string }) {
  return (
    <div
      role="alert"
      style={{
        margin: '1rem',
        padding: '1rem 1.25rem',
        border: '2px dashed #b3261e',
        borderRadius: 8,
        background: '#fdecea',
        color: '#8c1d18',
        fontFamily: 'monospace',
        fontSize: '0.85rem',
      }}
    >
      no renderer for <strong>{id}</strong>
      <br />
      registered: {registeredIds().join(', ') || '(none)'}
    </div>
  );
}

export default function PageRenderer({ host }: { host: AdminHost }) {
  const { spec, loaded } = useDashboardSpec('admin');

  // Granted widget ids for the active page, in catalog order. Strictly
  // spec-driven once the spec is loaded; full static composition otherwise.
  const ids: string[] = (loaded && spec)
    ? (spec.pages.find(p => p.id === `admin/${host.active}`)?.widgets.map(w => w.id) ?? [])
    : (FALLBACK_PAGE_WIDGETS[host.active] ?? []);

  return (
    <>
      {ids.map(id => {
        const widget = getWidget(id);
        return widget
          ? <widget.Component key={id} host={host} />
          : <MissingWidget key={id} id={id} />;
      })}
    </>
  );
}
