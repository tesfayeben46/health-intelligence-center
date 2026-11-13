/**
 * AppShell: app-style renderer for entity-CRUD and master-detail pages. It wraps
 * the grid runtime with two layers:
 *
 *   - content layer: renders the page (PageRenderer.renderPage) unless a
 *     master-detail route is active (host.openDetail), in which case it shows the
 *     registered DetailDef for that kind; host.closeDetail returns to the page.
 *   - modal layer: a single modal slot filled by the registered ModalDef when
 *     host.openModal fires. The shell draws the backdrop and frame, the ModalDef
 *     fills the body; host.closeModal, a backdrop click or the close button
 *     dismisses it.
 *
 * Both layers react to the host shell store (subscribeShell). Plain grid pages
 * call renderPage directly (CatalogPage); the app-shell is opt-in. Depends only
 * on other runtime/* modules and api/spec.
 */
import type { ResolvedPage } from '../api/spec';
import type {
  DetailDef, DetailCtx, DetailRoute, ModalDef, ModalCtx, ModalRequest,
} from './types';
import { renderPage, emptyState } from './PageRenderer';
import { getDetailDef } from './detailRegistry';
import { getModalDef } from './modalRegistry';
import { host, subscribeShell } from './host';
import { resolveTheme } from './theme';
import { filterBus } from './filterBus';
import { fetchEntity } from './dataHook';

/** Run a def's render and adopt its (possibly async) cleanup function, using the
 *  same disposal guard PageRenderer uses for widgets. Returns a teardown. */
function mountRender(
  run: () => void | (() => void) | Promise<void | (() => void)>,
): () => void {
  let cleanup: void | (() => void);
  let disposed = false;
  const result = run();
  if (result instanceof Promise) {
    result.then(c => {
      if (!disposed) cleanup = c;
      else if (typeof c === 'function') { try { c(); } catch { /* noop */ } }
    });
  } else {
    cleanup = result;
  }
  return () => {
    disposed = true;
    try { if (typeof cleanup === 'function') cleanup(); } catch { /* noop */ }
  };
}

/** Mount a detail view for a route into `el`. */
function mountDetail(def: DetailDef, route: DetailRoute, page: ResolvedPage, el: HTMLElement): () => void {
  const ctx: DetailCtx = {
    el,
    route,
    page,
    theme: resolveTheme(),
    filters: filterBus.get(),
    bus: filterBus,
    host,
    fetchEntity: (path, params) => fetchEntity(path, params),
    empty: (m) => emptyState(el, m),
  };
  return mountRender(() => def.render(ctx));
}

/** Mount a modal body into `el`. */
function mountModal(def: ModalDef, req: ModalRequest, page: ResolvedPage, el: HTMLElement): () => void {
  const ctx: ModalCtx = {
    el,
    props: req.props || {},
    page,
    theme: resolveTheme(),
    host,
    fetchEntity: (path, params) => fetchEntity(path, params),
    close: () => host.closeModal(),
    empty: (m) => emptyState(el, m),
  };
  return mountRender(() => def.render(ctx));
}

/**
 * Render a page under the app-shell. Returns a teardown that disposes the page /
 * detail / modal and unsubscribes. Drop-in alternative to renderPage for a page
 * that uses master-detail and/or modals.
 */
export function renderAppShell(page: ResolvedPage, mount: HTMLElement): () => void {
  mount.innerHTML = '';
  const content = document.createElement('div');
  content.className = 'shell-content';
  const modalSlot = document.createElement('div');
  modalSlot.className = 'shell-modal-slot';
  modalSlot.style.display = 'none';
  mount.appendChild(content);
  mount.appendChild(modalSlot);

  let contentTeardown: () => void = () => {};
  let modalTeardown: () => void = () => {};
  // Track what is currently rendered so a shell change re-renders only the layer
  // that actually changed (openDetail/closeDetail/openModal/closeModal each
  // assign a fresh object or null, so reference comparison detects the change).
  let shownDetail: DetailRoute | null = null;
  let shownModal: ModalRequest | null = null;
  let contentDrawn = false;

  const renderContent = () => {
    contentTeardown(); contentTeardown = () => {};
    content.innerHTML = '';
    const detail = host.currentDetail;
    if (detail) {
      const slot = document.createElement('div');
      slot.className = 'shell-detail';
      content.appendChild(slot);
      const def = getDetailDef(detail.kind);
      if (!def) {
        slot.innerHTML =
          `<div class="widget-empty" style="padding:3rem">No detail view registered for <code>${detail.kind}</code></div>`;
      } else {
        contentTeardown = mountDetail(def, detail, page, slot);
      }
    } else {
      contentTeardown = renderPage(page, content);
    }
  };

  const renderModal = () => {
    modalTeardown(); modalTeardown = () => {};
    modalSlot.innerHTML = '';
    const req = host.currentModal;
    if (!req) { modalSlot.style.display = 'none'; return; }
    modalSlot.style.display = '';

    const def = getModalDef(req.id);
    const backdrop = document.createElement('div');
    backdrop.className = 'shell-modal-backdrop';
    const frame = document.createElement('div');
    frame.className = 'shell-modal-frame card card-elev';

    // Frame title: a caller-supplied props.title wins over the ModalDef default.
    const title = (req.props && req.props.title) || def?.title || '';
    const head = document.createElement('div');
    head.className = 'shell-modal-hd';
    head.innerHTML =
      `<h3 class="shell-modal-title">${String(title).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c] as string))}</h3>` +
      `<button class="shell-modal-x" type="button" aria-label="Close">✕</button>`;
    const bodyEl = document.createElement('div');
    bodyEl.className = 'shell-modal-body';

    frame.appendChild(head);
    frame.appendChild(bodyEl);
    backdrop.appendChild(frame);
    modalSlot.appendChild(backdrop);

    // Dismiss on the close button or a click on the backdrop (not the frame).
    head.querySelector<HTMLButtonElement>('.shell-modal-x')!
      .addEventListener('click', () => host.closeModal());
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) host.closeModal(); });

    if (!def) {
      bodyEl.innerHTML =
        `<div class="widget-empty" style="padding:2rem">No modal registered for <code>${req.id}</code></div>`;
    } else {
      modalTeardown = mountModal(def, req, page, bodyEl);
    }
  };

  const sync = () => {
    if (!contentDrawn || host.currentDetail !== shownDetail) {
      shownDetail = host.currentDetail;
      contentDrawn = true;
      renderContent();
    }
    if (host.currentModal !== shownModal) {
      shownModal = host.currentModal;
      renderModal();
    }
  };

  const unsub = subscribeShell(sync);
  sync();   // initial paint (page or, if a route is already set, the detail)

  return () => {
    unsub();
    contentTeardown();
    modalTeardown();
    mount.innerHTML = '';
  };
}
