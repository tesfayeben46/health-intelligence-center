/**
 * Workshop topbar: spec-driven page title, the Request-compute action
 * (opens the compute-request ModalDef through the runtime modal host), and the
 * user chip and sign-out from /auth/me (useAuth).
 *
 * There is no global "Open JupyterLab" button: labs are one per
 * project and open only from a project (ws-project-detail's "Open in
 * JupyterLab"). Running labs are listed on the Workspace page (ws-running-labs).
 */
import { useAuth } from '../api/useAuth';
import { host } from '../runtime/host';

function initials(name: string): string {
  return name.trim().split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase() || 'NH';
}

// One-line masthead description per page.
const PAGE_DESC: Record<string, string> = {
  Workspace: 'Your projects, compute and community at a glance. Open a lab, build a dataset, or pick up where you left off.',
  Datasets:  'Build governed datasets from the marts and manage the ones you have generated.',
  Models:    'Browse the model catalogue, read each model’s input/output schema and run one on your own inputs.',
  Projects:  'Create, manage, and collaborate on data science projects.',
  Community: 'Groups share artifacts at the shared tier. Manage invitations, requests and memberships.',
  Help:      'How-to guides and reference for working in the workshop.',
  Settings:  'Your profile and personal preferences.',
};

export default function Topbar({ pageTitle }: { pageTitle: string }) {
  const { user, logout } = useAuth();

  const displayName = user?.full_name || user?.username || '';
  const displayRole = user?.primary_role === 'R-CONS-SCI' ? 'Researcher'
                    : user?.primary_role === 'R-CONS-RES' ? 'External researcher'
                    : user?.primary_role || '';

  return (
    <header className="ws-topbar">
      <div className="tb-title">
        <div className="breadcrumb">
          <span>Workshop</span>
          <svg className="ico" style={{ width: '11px', height: '11px', opacity: 0.55 }}><use href="#i-arrow-right" /></svg>
          <span>{pageTitle}</span>
        </div>
        <h2>{pageTitle}</h2>
        {PAGE_DESC[pageTitle] && <p className="tb-desc">{PAGE_DESC[pageTitle]}</p>}
      </div>

      <div className="tb-actions">
        <button
          className="tb-compute"
          type="button"
          onClick={() => host.openModal('compute-request')}
          title="Request a compute allocation for your workspace"
        >
          <svg className="ico"><use href="#i-server" /></svg>
          Request compute
        </button>

        <button className="tb-user" type="button" title={user?.kc_sub ? `kc_sub: ${user.kc_sub} · auth: ${user.auth_mode}` : undefined}>
          <span className="avatar">{initials(displayName)}</span>
          <span className="meta">
            <span className="nm">{displayName}</span>
            {' '}
            <span className="rl">{displayRole}</span>
          </span>
        </button>
        <a href="#" onClick={(e) => { e.preventDefault(); logout(); }} className="tb-signout" title="Sign out">
          <svg className="ico ico-sm"><use href="#i-power" /></svg>
        </a>
      </div>
    </header>
  );
}
