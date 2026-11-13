/**
 * React hook that subscribes to the singleton biApp instance.
 *
 * biApp() is a plain JavaScript factory ported from Alpine: it manages
 * its own state mutations. We expose helpers (setActive, setFilter,
 * setOpenFilter, setAlertModal, etc.) that mutate biApp and call
 * notify() so React re-renders.
 */
import { useEffect, useSyncExternalStore } from 'react';
import { getBiApp, subscribe, notify, initBiApp } from './biApp';

type BiSnapshot = {
  active: string;
  openFilter: string | null;
  alertModal: boolean;
  notifPanel: boolean;
  filters: { geography: string; period: string; programme: string };
  alertSubs: any[];
  alertNotifications: any[];
  alertNotifUnread: number;
  compactDensity: boolean;
};

let _snap: BiSnapshot | null = null;

function makeSnapshot(): BiSnapshot {
  const a = getBiApp();
  return {
    active: a.active,
    openFilter: a.openFilter,
    alertModal: a.alertModal,
    notifPanel: a.notifPanel,
    filters: { ...a.filters },
    alertSubs: a.alertSubs.slice(),
    alertNotifications: a.alertNotifications.slice(),
    alertNotifUnread: a.alertNotifUnread,
    compactDensity: a.compactDensity,
  };
}

function getSnapshot(): BiSnapshot {
  if (!_snap) _snap = makeSnapshot();
  return _snap;
}

// Wire-up: subscribe replaces snapshot so React detects change by reference.
subscribe(() => { _snap = makeSnapshot(); });

export function useBiApp(): { state: BiSnapshot; app: any; setActive: (id: string) => void; setOpenFilter: (k: string | null) => void; setAlertModal: (v: boolean) => void; setNotifPanel: (v: boolean) => void; setFilter: (k: string, v: string) => void; } {
  const state = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const app = getBiApp();
  return {
    state,
    app,
    setActive(id: string) {
      app.active = id;
      notify();
      // Set the page, then renderActive() on the next microtask.
      // Single microtask call: do not also call renderActive from App's
      // useEffect or the paginated_table widget re-renders with ws.sort set.
      queueMicrotask(() => app.renderActive?.());
    },
    setOpenFilter(k: string | null) { app.openFilter = k; notify(); },
    setAlertModal(v: boolean) { app.alertModal = v; notify(); },
    setNotifPanel(v: boolean) { app.setNotifPanel(v); },
    setFilter(k: string, v: string) {
      // biApp.setFilter mutates state and schedules its own renderActive via
      // the patched $nextTick. We notify React; biApp handles the render.
      app.setFilter(k, v); notify();
    },
  };
}

export function useBootBiApp(): void {
  useEffect(() => {
    initBiApp();
    notify();
  }, []);
}
