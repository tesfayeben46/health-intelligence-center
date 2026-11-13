/**
 * Public dashboard · page renderer.
 *
 * Given the active page, render its `fullBleed` widgets bare, then wrap its
 * `content` widgets in the single `<div className="pub-content">` that the
 * base.css / home.css rules target. Widgets are looked up by id in the
 * registry; an unknown id renders a visible placeholder, so a missing renderer
 * is obvious in the page rather than a blank gap.
 */
import { useEffect, useState } from 'react';
import type { PublicHost } from './types';
import { PAGES } from './pages';
import { getWidget, registeredIds } from './registry';
import { useProfile } from '../hooks/useProfile';

/**
 * The public page composition (pages.ts) is static, but WHICH of those widgets a
 * country shows is the same profile decision internal-bi makes: the active
 * profile's catalog.yaml `disable:` list, served at /api/proxy/profile/catalog.
 * Fetch it once (module load) so a country-specific widget (pub-zm-supply on
 * eLMIS) and the one it replaces (pub-insights on DHIS2) can both live in
 * pages.ts and the profile picks: no widget-side change.
 */
let _disabled: Promise<Set<string>> | null = null;
function fetchDisabled(): Promise<Set<string>> {
  if (!_disabled) {
    _disabled = fetch('/api/proxy/profile/catalog')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => new Set<string>(((d?.disable?.widget ?? []) as { id: string }[]).map((w) => w.id)))
      .catch(() => new Set<string>());
  }
  return _disabled;
}
fetchDisabled();  // start the fetch before first render to avoid a wrong-country flash

function useDisabled(): { set: Set<string>; ready: boolean } {
  const [state, setState] = useState<{ set: Set<string>; ready: boolean }>({ set: new Set(), ready: false });
  useEffect(() => {
    let on = true;
    fetchDisabled().then((s) => { if (on) setState({ set: s, ready: true }); });
    return () => { on = false; };
  }, []);
  return state;
}

/**
 * Render a single widget by id, or a VISIBLE placeholder if the id isn't in the
 * registry. Exported so chrome (GovBar/Footer) and modals go through the same
 * never-silently-drop path as page content.
 */
export function WidgetSlot({ id, host }: { id: string; host: PublicHost }) {
  const widget = getWidget(id);
  if (!widget) {
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
  const Component = widget.Component;
  return <Component host={host} />;
}

export default function PageRenderer({ host }: { host: PublicHost }) {
  const spec = PAGES[host.active];
  const { set: disabled, ready } = useDisabled();
  const { profile } = useProfile();
  const keep = (id: string) => !disabled.has(id);
  const fullBleed = (spec?.fullBleed ?? []).filter(keep);
  // Hold profile-switchable content until the disable list is known, so a
  // wrong-country widget never flashes before it is filtered out.
  const content = ready ? (spec?.content ?? []).filter(keep) : [];
  // A page whose every widget the active profile disables (e.g. a data-backed
  // page under a country whose data layer is not yet ingested) renders a
  // short note rather than a blank page, so the nav link does not lead nowhere.
  const specHadContent = (spec?.content ?? []).length > 0;
  const allDisabled = ready && specHadContent && content.length === 0 && fullBleed.length === 0;

  return (
    <>
      {fullBleed.map((id) => (
        <WidgetSlot key={id} id={id} host={host} />
      ))}
      {content.length > 0 && (
        <div className="pub-content">
          {content.map((id) => (
            <WidgetSlot key={id} id={id} host={host} />
          ))}
        </div>
      )}
      {allDisabled && (
        <div className="pub-content">
          <div className="od-empty" style={{ minHeight: 220, display: 'flex', flexDirection: 'column',
                 alignItems: 'center', justifyContent: 'center', textAlign: 'center' }}>
            <p className="text-sm muted">
              This section is not yet available{profile.country?.name ? ` for ${profile.country.name}` : ''}.
            </p>
          </div>
        </div>
      )}
    </>
  );
}
