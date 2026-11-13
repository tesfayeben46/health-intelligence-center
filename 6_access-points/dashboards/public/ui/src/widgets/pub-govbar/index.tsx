/**
 * Widget · pub-govbar: Government top bar (2-tier utility + brand+nav).
 * Reads active/setPage/openModal from the host. Persistent chrome (CHROME_TOP).
 */
import type { PublicWidget, PublicHost } from '../../runtime/types';
import { useProfile, logoUrl } from '../../hooks/useProfile';

function GovBar({ host }: { host: PublicHost }) {
  const { active, setPage, openModal } = host;
  const { profile, copy } = useProfile();
  const t = copy.govbar;
  const logo = logoUrl(profile);
  return (
    <>
      <a href="#main" className="gov-skip">{t.skip}</a>
      <header className="gov">
        <div className="gov-row">
          <a
            href="#"
            className="gov-mark"
            onClick={(e) => { e.preventDefault(); setPage('home'); }}
          >
            {logo && (
              <img className="gov-mark-logo" src={logo} alt="" aria-hidden={true} />
            )}
            <span className="gov-mark-text">{t.mark_text}</span>
            <span className="gov-mark-tag">{t.mark_tag}</span>
          </a>

          <nav className="gov-nav" aria-label="Primary">
            <a
              href="#"
              className={active === 'home' ? 'active' : undefined}
              onClick={(e) => { e.preventDefault(); setPage('home'); }}
            >{t.nav.home}</a>
            <a
              href="#"
              className={active === 'data' ? 'active' : undefined}
              onClick={(e) => { e.preventDefault(); setPage('data'); }}
            >{t.nav.data}</a>
            <a
              href="#"
              className={(active === 'articles' || active === 'article') ? 'active' : undefined}
              onClick={(e) => { e.preventDefault(); setPage('articles'); }}
            >{t.nav.articles}</a>
          </nav>

          <div className="gov-cta">
            <button className="gov-link" onClick={() => openModal('access')}>{t.request_access}</button>
            <button className="gov-signin" onClick={() => openModal('dashboards')}>{t.dashboards}</button>
          </div>
        </div>
      </header>
    </>
  );
}

const widget: PublicWidget = { id: 'pub-govbar', Component: GovBar };
export default widget;
