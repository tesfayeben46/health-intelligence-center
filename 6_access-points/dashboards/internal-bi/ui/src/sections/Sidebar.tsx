/**
 * Internal-BI sidebar.
 *
 * Nav items are filtered by the resolved dashboard spec, so pages the role
 * cannot access are hidden. Until the spec has loaded, every page is shown.
 */
import { useEffect, useState } from 'react';
import { useBiApp } from '../useBiApp';
import { useDashboardSpec } from '../api/spec';

const PAGES = [
  { id: 'overview', ico: 'i-grid',       title: 'Overview',           page_id: 'internal-bi/overview' },
  { id: 'disease',  ico: 'i-alert',      title: 'Disease Surveillance', page_id: 'internal-bi/disease' },
  { id: 'resource', ico: 'i-package',    title: 'Resource Management', page_id: 'internal-bi/resource' },
  { id: 'control-tower', ico: 'i-truck', title: 'Supply Control Tower', page_id: 'internal-bi/control-tower' },
  { id: 'service',  ico: 'i-chart-line', title: 'Service Performance', page_id: 'internal-bi/service' },
  { id: 'export',   ico: 'i-upload',     title: 'Data Export & Query', page_id: 'internal-bi/export' },
];

export default function Sidebar() {
  // The crest and the word-mark are profile facts. internal-BI already
  // fetches the profile for its reference tables, so this reads the same
  // endpoint rather than adding a second source for the same answer.
  const [logoSrc, setLogoSrc] = useState<string | null>(null);
  const [platformAbbr, setPlatformAbbr] = useState('Palladium');
  useEffect(() => {
    let live = true;
    fetch('/gw/profile').then(r => (r.ok ? r.json() : null)).then(p => {
      if (!live || !p) return;
      setLogoSrc(p.ministry?.logo
        ? `/gw/profile/asset/${encodeURIComponent(p.ministry.logo)}` : null);
      if (p.platform?.abbr) setPlatformAbbr(p.platform.abbr);
    }).catch(() => {});
    return () => { live = false; };
  }, []);
  const { state, setActive } = useBiApp();
  const { spec, loaded } = useDashboardSpec('internal-bi');

  // Spec not loaded yet: show all. Once loaded, hide pages the role does not
  // appear in. The resolver (permissions.resolve_dashboard) drops any page
  // with zero visible content sections, so a page present in spec.pages is
  // guaranteed to have visible content and this presence filter is exact.
  const visiblePageIds = loaded && spec
    ? new Set(spec.pages.map(p => p.id))
    : null;

  const visible = visiblePageIds
    ? PAGES.filter(p => visiblePageIds.has(p.page_id))
    : PAGES;

  return (
    <aside className="bi-sidebar">
      <div className="bi-sidehead">
        <a href="/">
          {logoSrc
            ? <img src={logoSrc} alt="" className="sidehead-logo" />
            : null}
          <div className="sidehead-text">
            <div className="title">{platformAbbr}</div>
            <div className="subtitle">Internal BI</div>
          </div>
        </a>
      </div>

      <nav className="bi-nav" style={{ marginTop: '1rem' }}>
        {visible.map(p => (
          <a key={p.id} className={state.active === p.id ? 'active' : undefined} onClick={() => setActive(p.id)}>
            <svg className="ico"><use href={'#' + p.ico} /></svg>
            <span>{p.title}</span>
          </a>
        ))}
      </nav>

      {/* Static product identity only. */}
      <div className="bi-sidebar-foot">
        <strong>Palladium v1.0</strong> · PROD<br />
        <span>Data Intelligence Platform</span>
      </div>
    </aside>
  );
}
