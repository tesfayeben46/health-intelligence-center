/**
 * Widget · pub-dashboards-modal: a DIRECTORY / launcher for the protected
 * surfaces. Re-homed verbatim from sections/DashboardsModal.tsx.
 *
 * This is not a sign-in form. It only lets the visitor pick which dashboard
 * to open; clicking a card navigates to that dashboard, and the real login
 * (username & password / Google / EHIA SAML) happens on that dashboard's own
 * Keycloak login page. If the visitor already has a live SSO session they are
 * taken straight in: the dashboard's useAuth silently completes the flow.
 *
 * Destination URLs resolve at runtime from /api/access-points (see
 * hooks/accessPoints) so the shipped bundle links at the nip.io Ingress hosts,
 * not compile-time localhost. Reads closeModal + openModal from the host.
 */
import { useEffect, useState } from 'react';
import { loadSiblingBases, fallbackBases, type SiblingBases } from '../../hooks/accessPoints';
import { useProfile } from '../../hooks/useProfile';
import type { PublicWidget, PublicHost } from '../../runtime/types';

type DashboardSpec = {
  label: string;
  base: keyof SiblingBases;
  audience: string;
  description: string;
  icon: JSX.Element;
};

const BI_ICON = (
  <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M3 3v18h18" />
    <rect x="7" y="11" width="3" height="6" rx="0.5" />
    <rect x="12" y="7" width="3" height="10" rx="0.5" />
    <rect x="17" y="13" width="3" height="4" rx="0.5" />
  </svg>
);

const ADMIN_ICON = (
  <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M12 2l8 4v5c0 5-3.4 8.5-8 11-4.6-2.5-8-6-8-11V6l8-4z" />
    <path d="M9 12l2 2 4-4" />
  </svg>
);

const WORKSHOP_ICON = (
  <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <circle cx="9" cy="8" r="3" />
    <path d="M3 20v-1a5 5 0 0 1 5-5h2a5 5 0 0 1 5 5v1" />
    <circle cx="17.5" cy="9.5" r="2.2" />
    <path d="M15 20v-.5a4 4 0 0 1 6-3.4" />
  </svg>
);

const CHEVRON = (
  <svg className="dash-card-go" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M9 6l6 6-6 6" />
  </svg>
);

// `audience` here is only a last-resort fallback; the live value comes from the
// active profile's copy deck (copy.dashboards.*) so a country whose sub-national
// bodies are named differently (RHB in Ethiopia, Provincial Health Office in
// Zambia) says so without a rebuild.
const DASHBOARDS: DashboardSpec[] = [
  {
    label: 'Internal Business Intelligence',
    base: 'internal-bi',
    audience: 'Ministry staff · programme directors',
    description: 'Programme analytics, cascade, supply and equity dashboards.',
    icon: BI_ICON,
  },
  {
    label: 'Infrastructure & Access Management',
    base: 'admin',
    audience: 'IT operations · security officers',
    description: 'Container health, user & role admin, audit logs, SSO config.',
    icon: ADMIN_ICON,
  },
  {
    label: 'Community Workshop Portal',
    base: 'workshop',
    audience: 'Researchers · analysts · community contributors',
    description: 'Projects, datasets browser, model catalogue and review.',
    icon: WORKSHOP_ICON,
  },
];

function DashboardsModal({ host }: { host: PublicHost }) {
  const onClose = host.closeModal;
  const openAccess = () => host.openModal('access');
  const { copy } = useProfile();
  // Profile-supplied audiences, keyed by dashboard, falling back to the spec.
  const audienceFor = (base: keyof SiblingBases, fallback: string): string => {
    const d = copy?.dashboards ?? {};
    const key = base === 'internal-bi' ? 'bi_audience'
              : base === 'admin' ? 'admin_audience'
              : base === 'workshop' ? 'workshop_audience' : '';
    return (key && typeof d[key] === 'string' && d[key]) ? d[key] : fallback;
  };

  // Seed with the same-origin fallback so the cards work immediately, then
  // refine with the server-resolved bases once /api/access-points responds.
  const [bases, setBases] = useState<SiblingBases>(fallbackBases);
  useEffect(() => { loadSiblingBases().then(setBases); }, []);

  const onBackdrop = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) onClose();
  };
  const go = (base: keyof SiblingBases) => {
    // Navigate to the dashboard; it drives its own Keycloak login (or, with a
    // live SSO session, takes the visitor straight in). No IdP choice here.
    window.location.assign(bases[base]);
  };

  return (
    <div className="modal-bg" onClick={onBackdrop}>
      <div className="modal" style={{ maxWidth: 560 }}>
        <div className="modal-hdr">
          <h2>Open a dashboard</h2>
          <p>Choose a workspace. You'll sign in on the next screen.</p>
        </div>
        <div className="modal-body">
          <div className="dash-card-stack">
            {DASHBOARDS.map((d) => (
              <button key={d.base} className="dash-card" onClick={() => go(d.base)}>
                <span className="dash-card-ico">{d.icon}</span>
                <span className="dash-card-text">
                  <span className="dash-card-title">{d.label}</span>
                  <span className="dash-card-desc">{d.description}</span>
                  <span className="dash-card-aud">{audienceFor(d.base, d.audience)}</span>
                </span>
                {CHEVRON}
              </button>
            ))}
          </div>

          <p className="text-xs muted mt-md" style={{ textAlign: 'center', paddingTop: '0.8rem', borderTop: '1px solid var(--border)' }}>
            Don't have access yet? <a href="#" onClick={(e) => { e.preventDefault(); openAccess(); }}>Request access</a>
          </p>
        </div>
        <div className="modal-foot">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

const widget: PublicWidget = { id: 'pub-dashboards-modal', Component: DashboardsModal };
export default widget;
