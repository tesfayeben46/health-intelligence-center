/**
 * Admin imperative engine: an Alpine-style state factory in a TS module with a
 * React subscribe/notify layer (same pattern as internal-bi's biApp.ts).
 *
 * It holds the nav breadcrumb, the access-request review modal and the Backups
 * ECharts widget. The infra topology renders separately in widgets/adm-topology
 * from /gw/topology.
 *
 * @ts-nocheck
 */
// @ts-nocheck
/* eslint-disable */

import {
  listAccessRequests, decideAccessRequest,
  type AccessRequestRow,
} from './api/accessRequests';
import { profileNow } from './hooks/useProfile';

/** How an access-decision email signs off. These reach real people, so the
 *  ministry named is the deployment's own rather than one country's. */
const signOff = (): string => {
  const p = profileNow();
  return [p.ministry?.name, p.platform?.abbr].filter(Boolean).join(' · ');
};

// Public-form dashboard keys to display labels for the queue pills.
const _DASH_LABELS: Record<string, string> = {
  'internal-bi': 'Internal BI', 'infra-admin': 'Infra Admin',
  'community-workshop': 'Workshop portal', 'submission-portal': 'Submission portal',
  'dbt-docs': 'dbt Docs', 'research-api': 'Research-tier API',
};
function _relTime(iso: string | null): string {
  if (!iso) return 'n/a';
  const t = Date.parse(iso); if (isNaN(t)) return 'n/a';
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
// Shape a gateway row into the UI row the tables render. Keeps both the uuid
// `id` (the key for decide/provision) and the human `code` (AR-YYYY-NNNN).
function _shapePending(r: AccessRequestRow) {
  return {
    id: r.id, code: r.tracking_id, submitted: _relTime(r.submitted_at),
    name: r.full_name || '(anonymous)', email: r.email || '',
    org: r.org || 'n/a', role: r.role || '',
    dashboards: (r.dashboards || []).map(d => _DASH_LABELS[d] || d),
    purpose: r.purpose || '', sla: 'n/a', slaClass: 'good',
    status: r.status, granted_role: r.granted_role,
    granted_scope: r.granted_scope, provisioning_status: r.provisioning_status,
    fayda_id: r.fayda_id, verified_via: r.verified_via,
    supervisorEmail: r.supervisor_email || '', irb: r.irb || '',
  };
}
function _shapeResolved(r: AccessRequestRow) {
  return {
    id: r.id, code: r.tracking_id, resolved: _relTime(r.decision_at),
    name: r.full_name || '(anonymous)', decision: r.status,
    approver: 'n/a', note: r.decision_note || '',
    granted_role: r.granted_role, provisioning_status: r.provisioning_status,
  };
}

declare global {
  interface Window {
    __adminApp?: any;
    echarts: any;
  }
}

const echarts: any = (typeof window !== 'undefined' ? (window as any).echarts : null);

function adminApp() {
  return {
    active: 'infra',

    newRequestId: null,
    showLinkedToast: false,

    // Topbar breadcrumb · derives group + title from the active nav item
    // Nav groups: Data = Quality & Lineage · Models ·
    // Review Queue; System & Access = Users and Access · Roles and
    // Permissions · SSO · Environment · Documentation. Titles mirror the
    // ops_perm page catalog (bundle/seed-pack/app/security/pages.yaml).
    pageInfo: {
      infra:               { group: 'Infrastructure',   title: 'Health & topology' },
      alerts:              { group: 'Infrastructure',   title: 'Alert routing' },
      dq:                  { group: 'Data',             title: 'Quality & lineage' },
      'models-datasets':   { group: 'Data',             title: 'Models and datasets' }, // catalog page admin/models-datasets (Models + Review merged)
      'users-access':      { group: 'Identity and Access',  title: 'Users and Access' },  // catalog page admin/users-access
      'roles-permissions': { group: 'Identity and Access',  title: 'Roles and Permissions' }, // catalog page admin/roles-permissions
      sso:                 { group: 'Identity and Access',  title: 'SSO & identity' },
      documentation:       { group: 'Identity and Access',  title: 'Documentation' },     // catalog page admin/documentation (pinned bottom)

      // MDM workspace: append-only section
      mdm:                 { group: 'Data',             title: 'MDM Stewardship' },     // catalog page admin/mdm
    },
    get navGroup() { return (this.pageInfo[this.active] || {}).group || 'Admin'; },
    get pageTitle() { return (this.pageInfo[this.active] || {}).title || 'Dashboard'; },

    // Access requests (/gw/admin/access-requests)
    // Pending queue and resolved history, loaded from the gateway.
    // Approve-with-role auto-provisions a Keycloak account + set-password email.
    requests: [] as any[],
    resolved: [] as any[],
    accessLoaded: false,
    accessError: '',

    async loadAccessRequests() {
      try {
        const [pending, approved, declined, info] = await Promise.all([
          listAccessRequests('pending'),
          listAccessRequests('approved'),
          listAccessRequests('declined'),
          listAccessRequests('info_requested'),
        ]);
        this.requests = pending.map(_shapePending);
        this.resolved = [...approved, ...declined, ...info]
          .sort((a, b) => String(b.decision_at || '').localeCompare(String(a.decision_at || '')))
          .map(_shapeResolved);
        this.accessLoaded = true; this.accessError = '';
      } catch (e: any) {
        this.accessError = String(e?.message || e);
      }
      notify();
    },

    get pendingRequestCount() { return this.requests.length; },

    // Review modal state
    selectedRequest: null,
    reviewDecision: '',                            // approved | declined | more-info
    reviewRole: 'R-CONS-ANA',
    reviewScope: { region: 'all', programme: 'all', duration: '6m' },
    reviewNote: '',
    showEmailPreview: false,
    adminToast: null,
    reviewBusy: false,
    reviewError: '',

    openReview(id) {
      this.selectedRequest = this.requests.find(r => r.id === id);
      this.reviewDecision = '';
      this.reviewRole = 'R-CONS-ANA';
      this.reviewScope = { region: 'all', programme: 'all', duration: '6m' };
      this.reviewNote = '';
      this.showEmailPreview = false;
      this.reviewError = '';
      document.documentElement.style.overflow = 'hidden';
      notify();
    },
    closeReview() {
      this.selectedRequest = null;
      this.showEmailPreview = false;
      document.documentElement.style.overflow = '';
      notify();
    },
    // Posts the decision to the gateway. Approve-with-role auto-provisions (Keycloak
    // account + role + scope + set-password email). The 'more-info' UI value
    // maps to the backend's 'info_requested'; reviewScope to granted_scope
    // (region_id + programme code; 'all' means unrestricted, no scope row).
    async submitReview() {
      if (!this.reviewDecision || !this.selectedRequest || this.reviewBusy) return;
      const req = this.selectedRequest;
      const decision = this.reviewDecision === 'more-info'
        ? 'info_requested' : this.reviewDecision;
      const granted_role = decision === 'approved' ? this.reviewRole : null;
      let granted_scope: any = null;
      if (decision === 'approved') {
        granted_scope = {};
        if (this.reviewScope.region && this.reviewScope.region !== 'all')
          granted_scope.geography = [this.reviewScope.region];
        if (this.reviewScope.programme && this.reviewScope.programme !== 'all')
          granted_scope.programme = [this.reviewScope.programme];
      }
      this.reviewBusy = true; this.reviewError = ''; notify();
      try {
        const res = await decideAccessRequest(req.id, {
          decision, granted_role, granted_scope, note: this.reviewNote || null,
        });
        const prov = res.provisioning;
        const emailNote = prov
          ? (prov.emailed ? `· set-password email sent to ${req.email}`
                          : '· account created, but the email was not sent (check SMTP)')
          : '';
        const verb = ({ approved: 'Approved & provisioned', declined: 'Declined',
                        info_requested: 'More info requested for' })[decision];
        this.adminToast = `${verb} ${req.code} ${emailNote}`.trim();
        this.closeReview();
        await this.loadAccessRequests();
        setTimeout(() => { this.adminToast = null; notify(); }, 7000);
      } catch (e: any) {
        this.reviewError = String(e?.message || e);
      } finally {
        this.reviewBusy = false; notify();
      }
    },

    decisionVerb()   { return ({approved:'Approved', declined:'Declined', 'more-info':'More information requested'})[this.reviewDecision] || ''; },
    roleLabel(r) {
      return ({
        'R-CONS-ANA':  'R-CONS-ANA · Analyst (read-only)',
        'R-CONS-SCI':  'R-CONS-SCI · Data scientist (Jupyter + read)',
        'R-CONS-RES':  'R-CONS-RES · Researcher (Jupyter + agg.)',
        'R-CONS-PRT':  'R-CONS-PRT · Partner (aggregate only)',
        'R-DEC':       'R-DEC · Decision-maker (full BI)',
      })[r] || r;
    },
    emailSubject() {
      if (!this.selectedRequest) return '';
      return ({
        approved:  `Your NHDW access request ${this.selectedRequest.id} has been approved`,
        declined:  `Your NHDW access request ${this.selectedRequest.id} was not approved`,
        'more-info': `More information needed · ${this.selectedRequest.id}`,
      })[this.reviewDecision] || '';
    },
    emailBody() {
      if (!this.selectedRequest) return '';
      const r = this.selectedRequest;
      const role = this.roleLabel(this.reviewRole);
      if (this.reviewDecision === 'approved') {
        return `Dear ${r.name},\n\n`
             + `Your access request to the National Health Data Warehouse has been approved.\n\n`
             + `  Tracking ID: ${r.id}\n`
             + `  Granted role: ${role}\n`
             + `  Geographic scope: ${this.reviewScope.region === 'all' ? 'all regions' : this.reviewScope.region}\n`
             + `  Programme scope: ${this.reviewScope.programme === 'all' ? 'all programmes' : this.reviewScope.programme}\n`
             + `  Access duration: ${this.reviewScope.duration}\n\n`
             + `Complete your first-time login at: https://nhdw.local/login/setup?token=XXXX (valid 72 hours)\n\n`
             + (this.reviewNote ? `Reviewer note: ${this.reviewNote}\n\n` : ``)
             + `If you have any questions, please reply to this email.\n\n`
             + `${signOff()}\n`;
      }
      if (this.reviewDecision === 'declined') {
        return `Dear ${r.name},\n\n`
             + `Your access request to the National Health Data Warehouse (${r.id}) has not been approved at this time.\n\n`
             + (this.reviewNote ? `Reason: ${this.reviewNote}\n\n` : `Reason: Access criteria for the requested dashboards were not fully met.\n\n`)
             + `You may submit a new request once the noted concerns are addressed.\n\n`
             + `${signOff()}\n`;
      }
      return `Dear ${r.name},\n\n`
           + `Thank you for your access request to the NHDW (${r.id}). Before we can complete the review, we need additional information:\n\n`
           + `  ${this.reviewNote || 'Please confirm your institutional affiliation and intended use.'}\n\n`
           + `Please reply to this email with the requested details. We will resume the review on receipt.\n\n`
           + `${signOff()}\n`;
    },

    init() {
      // If we came from the public dashboard's request form, the URL will
      // carry ?new_request=AR-...  -- highlight that row + show toast + route to access requests
      const params = new URLSearchParams(window.location.search);
      const newRid = params.get('new_request');
      if (newRid) {
        this.newRequestId = newRid;
        this.showLinkedToast = true;
        this.active = 'users-access';   // access requests live on Users and Access
        setTimeout(() => { this.showLinkedToast = false; }, 9000);
      }
      // MDM workspace: append-only section
      // #mdm/<tab> deep link: open MDM Stewardship; the widget picks the tab.
      if (!newRid && window.location.hash.startsWith('#mdm')) this.active = 'mdm';
      this.loadAccessRequests();          // real pending queue + resolved history
      this.$nextTick(()=> this.renderActive());
      window.addEventListener('resize', ()=> this.renderActive());
    },

    renderActive() {
      // Wait one tick so x-show flushes before chart containers are measured.
      this.$nextTick(() => {
        ({
          infra:               () => {},
          alerts:              ()=>{},
          dq:                  ()=>{},
          'models-datasets':   ()=>{},
          'users-access':      () => this.loadAccessRequests(),  // refresh queue on entry
          'roles-permissions': ()=>{},
          sso:                 ()=>{},
          documentation:       ()=>{},
        })[this.active]?.call(this);
      });
    },

  };
}

// React subscription layer
const _subscribers = new Set<() => void>();
export function subscribe(fn: () => void): () => void {
  _subscribers.add(fn);
  return () => _subscribers.delete(fn);
}
export function notify(): void {
  for (const fn of _subscribers) fn();
}

let _instance: any = null;
export function getAdminApp(): any {
  if (_instance) return _instance;
  _instance = adminApp();
  _instance.$nextTick = (fn: () => void) => queueMicrotask(fn);
  return _instance;
}

export function initAdminApp(): any {
  const app = getAdminApp();
  if (!(window as any).__adminApp) {
    (window as any).__adminApp = app;
    app.init?.();
  }
  return app;
}
