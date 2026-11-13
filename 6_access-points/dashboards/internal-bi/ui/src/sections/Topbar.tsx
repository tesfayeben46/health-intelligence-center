/**
 * Internal-BI topbar: page title and subtitle, avatar and name from
 * /auth/me (with neutral fallbacks when those fields are missing).
 */
import { useBiApp } from '../useBiApp';
import { useAuth } from '../api/useAuth';

const PAGE_TITLES: Record<string, string> = {
  overview: 'Overview Dashboard',
  disease:  'Disease Surveillance',
  resource: 'Resource Management',
  'control-tower': 'Supply Control Tower',
  service:  'Service Performance',
  export:   'Data Export & Query',
  settings: 'Profile & Settings',
};

// Per-page masthead subtitle.
const PAGE_SUBTITLES: Record<string, string> = {
  overview: 'Key health-system indicators, action priorities, and system trust.',
  disease:  'Disease trends, risk distribution, response readiness, and active operations.',
  resource: 'Supply-chain and commodity readiness, and the service impact of stockouts.',
  'control-tower': 'Orders, delivery runs and stock risk from district stores to facilities.',
  service:  'Track quality, access, and outcomes across programmes and facilities.',
};

function initials(name: string): string {
  return name.trim().split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase() || 'DA';
}

/* Friendly names for the role codes that are not country-specific. The scoped
 * ones (a regional-health-bureau director, a programme director) are named by
 * the deployment's own roles, which travel with the appsec layer of a profile:
 * so they are not listed here. An unlisted code is humanised from the code
 * itself rather than shown raw, which reads acceptably in any country and
 * invents nothing. */
const ROLE_TITLES: Record<string, string> = {
  'R-DEC':         'Decision Maker',
  'R-CONS-ANA':    'M&E Analyst',
  'R-OPS-ADM':     'System Admin',
  'R-SUPER':       'Super User',
  'R-STEW':        'Data Steward',
};

/** 'R-RHB-LUSAKA' to 'RHB Lusaka'. Good enough to read, and true anywhere. */
function humaniseRole(code: string): string {
  return code.replace(/^R-/, '').split('-')
    .map(part => (part.length <= 4 ? part : part[0] + part.slice(1).toLowerCase()))
    .join(' ');
}

export default function Topbar() {
  const { app, state, setNotifPanel, setActive } = useBiApp();
  const { user, logout } = useAuth();
  const title = PAGE_TITLES[state.active] || '';
  const subtitle = PAGE_SUBTITLES[state.active] || '';
  // Neutral fallbacks for accounts without a name or job title: never an
  // invented identity.
  const displayName = user?.full_name || user?.username || 'Signed in';
  const displayRole = (user?.primary_role && ROLE_TITLES[user.primary_role]) ||
                      (user?.primary_role ? humaniseRole(user.primary_role) : '');
  return (
    <header className="bi-topbar">
      <div className="tb-title">
        <div className="breadcrumb">
          <span>Palladium</span>
          <span aria-hidden="true" style={{ opacity: 0.55 }}>›</span>
          <span>{title}</span>
        </div>
        <h2 style={{ fontFamily: 'var(--font-sans)', fontSize: '2rem', fontWeight: 800, letterSpacing: '-0.02em', lineHeight: 1.1 }}>{title}</h2>
        {subtitle && (
          <p style={{ margin: '0.2rem 0 0', fontSize: '0.92rem', fontWeight: 500, color: 'var(--ink-3)' }}>{subtitle}</p>
        )}
      </div>
      <div className="tb-actions">
        <button className="tb-iconbtn" onClick={() => setNotifPanel(!state.notifPanel)} title="Alert notifications" style={{ position: 'relative' }}>
          <svg className="ico"><use href="#i-bell" /></svg>
          {state.alertNotifUnread > 0 && (
            <span className="tb-badge">{state.alertNotifUnread}</span>
          )}
        </button>
        {/* Settings is a spec-governed page: don't navigate to it when the
            role isn't granted it (the chip still shows who is signed in). */}
        <button className="tb-user" onClick={() => { if (app.pageGranted('settings')) setActive('settings'); }} title={user?.kc_sub ? `kc_sub: ${user.kc_sub} · auth: ${user.auth_mode}` : 'Profile & settings'}>
          <div className="avatar">{initials(displayName)}</div>
          <div className="meta">
            <div className="nm">{displayName}</div>
            <div className="rl">{displayRole}</div>
          </div>
        </button>
        <a href="#" onClick={(e) => { e.preventDefault(); logout(); }}
           className="btn btn-sm btn-ghost" title="Sign out">
          <svg className="ico"><use href="#i-power" /></svg>
        </a>
      </div>
    </header>
  );
}
