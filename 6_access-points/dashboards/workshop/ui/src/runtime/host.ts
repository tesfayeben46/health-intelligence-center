/**
 * Host API: the surface a widget uses to drive the shell (navigate to a page,
 * focus or scroll to a widget, open a master-detail view or a registered modal).
 *
 * The workshop shell registers its own navigation callback
 * (registerHostNavigation, wired by App.tsx), so this module has no store
 * dependency.
 *
 * Widget cards carry `data-widget-id`, so focusWidget can find any rendered
 * widget on the current page and highlight it.
 */
import type { HostAPI, DetailRoute, ModalRequest } from './types';

let navigateFn: ((pageId: string) => void) | null = null;

/** The shell (App.tsx) registers how a catalog page id becomes the active
 *  page. Widgets only ever call host.navigateToPage. */
export function registerHostNavigation(fn: (pageId: string) => void): void {
  navigateFn = fn;
}

/*
 * App-shell routing store: the single owner of the master-detail route and the
 * open-modal request. Small pub/sub (one state owner, like filterBus): the host
 * methods below mutate it; renderAppShell subscribes and swaps the page/detail
 * and fills the modal slot. Inert until a widget calls openDetail/openModal.
 */
type ShellState = { detail: DetailRoute | null; modal: ModalRequest | null };
const shell: ShellState = { detail: null, modal: null };
const shellSubs = new Set<(s: ShellState) => void>();

function emitShell(): void {
  for (const cb of shellSubs) { try { cb(shell); } catch { /* noop */ } }
}

/** Subscribe to detail/modal route changes. Returns an unsubscribe fn. Used by
 *  renderAppShell; widgets never need this (they call the host open/close fns). */
export function subscribeShell(cb: (s: ShellState) => void): () => void {
  shellSubs.add(cb);
  return () => { shellSubs.delete(cb); };
}

export const host: HostAPI = {
  navigateToPage(pageId) {
    navigateFn?.(pageId);
  },
  focusWidget(widgetId) {
    const tryFocus = () => {
      const el = document.querySelector<HTMLElement>(`[data-widget-id="${widgetId}"]`);
      if (!el) return false;
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.add('widget-focus');
      setTimeout(() => el.classList.remove('widget-focus'), 2200);
      return true;
    };
    // If the widget isn't on the current page yet (navigation just fired),
    // retry briefly while the new page renders.
    if (tryFocus()) return;
    let n = 0;
    const iv = setInterval(() => { if (tryFocus() || ++n > 12) clearInterval(iv); }, 150);
  },

  // Master-detail routing
  openDetail(kind, id, props) {
    // A new object each time so renderAppShell detects the change by reference.
    shell.detail = { kind, id, ...(props ? { props } : {}) };
    emitShell();
  },
  closeDetail() {
    if (shell.detail === null) return;
    shell.detail = null;
    emitShell();
  },
  get currentDetail() { return shell.detail; },

  // Modal host
  openModal(id, props) {
    shell.modal = { id, ...(props ? { props } : {}) };
    emitShell();
  },
  closeModal() {
    if (shell.modal === null) return;
    shell.modal = null;
    emitShell();
  },
  get currentModal() { return shell.modal; },
};
