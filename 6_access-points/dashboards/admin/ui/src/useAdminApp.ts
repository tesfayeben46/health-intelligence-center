/**
 * React hook for the admin app (same pattern as internal-bi's useBiApp).
 *
 * renderActive() fires exactly once per setActive call, via a queueMicrotask
 * in the setter. Do not add a useEffect that also calls renderActive: widget
 * renderers initialise state on their first run and a second call re-applies it.
 */
import { useEffect, useSyncExternalStore } from 'react';
import { getAdminApp, subscribe, notify, initAdminApp } from './adminApp';

type AdminSnapshot = {
  active: string;
  showLinkedToast: boolean;
  newRequestId: string;
  pendingRequestCount: number;
  navGroup: string;
  pageTitle: string;
  // access-request review modal (reactive)
  reviewOpen: boolean;
  selectedRequest: any;
  reviewDecision: string;
  reviewRole: string;
  reviewScope: { region: string; programme: string; duration: string };
  reviewNote: string;
  showEmailPreview: boolean;
  reviewBusy: boolean;
  reviewError: string;
  adminToast: string;
  resolvedCount: number;
};

let _snap: AdminSnapshot | null = null;
function makeSnapshot(): AdminSnapshot {
  const a = getAdminApp();
  return {
    active: a.active,
    showLinkedToast: !!a.showLinkedToast,
    newRequestId: a.newRequestId || '',
    pendingRequestCount: a.pendingRequestCount || 0,
    navGroup: a.navGroup || '',
    pageTitle: a.pageTitle || '',
    reviewOpen: !!a.selectedRequest,
    selectedRequest: a.selectedRequest || null,
    reviewDecision: a.reviewDecision || '',
    reviewRole: a.reviewRole || 'R-CONS-ANA',
    reviewScope: { ...(a.reviewScope || { region: 'all', programme: 'all', duration: '6m' }) },
    reviewNote: a.reviewNote || '',
    showEmailPreview: !!a.showEmailPreview,
    reviewBusy: !!a.reviewBusy,
    reviewError: a.reviewError || '',
    adminToast: a.adminToast || '',
    resolvedCount: (a.resolved || []).length,
  };
}
function getSnapshot(): AdminSnapshot {
  if (!_snap) _snap = makeSnapshot();
  return _snap;
}
subscribe(() => { _snap = makeSnapshot(); });

export function useAdminApp() {
  const state = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const app = getAdminApp();
  return {
    state,
    app,
    setActive(id: string) {
      app.active = id;
      notify();
      queueMicrotask(() => app.renderActive?.());
    },
    closeToast() { app.showLinkedToast = false; notify(); },
    // Patch review-modal fields and re-render (the modal is a controlled form).
    setReview(patch: Record<string, any>) { Object.assign(app, patch); notify(); },
  };
}

export function useBootAdminApp(): void {
  useEffect(() => {
    initAdminApp();
    notify();
    queueMicrotask(() => getAdminApp().renderActive?.());
  }, []);
}
