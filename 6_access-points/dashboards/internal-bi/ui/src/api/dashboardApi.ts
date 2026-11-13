/**
 * SPA-side action surface for the BI chatbot.
 *
 * The chatbot's progressive tool-call protocol (driven by the gateway's
 * /api/chat) returns SPA-executor tools like `read_page`, `read_widget`,
 * `navigate_to_page`, `apply_global_filter`, etc. The ChatbotPanel
 * executes them by calling `window.__nhdwDashboard.<tool>(args)`.
 *
 * Every method returns `{ok, ...}` so the chatbot pipeline has a uniform
 * shape regardless of whether the action succeeded or hit an authorisation
 * / state error. Authorisation is enforced by the gateway (it only exposes
 * the tools the user is permitted to use); the SPA's job is to faithfully
 * execute the requested action.
 *
 * `page_id` follows the canonical "<dashboard>/<section>" format
 * (e.g. "internal-bi/disease"). Internally the SPA uses bare section IDs.
 */
import { getBiApp, notify } from '../biApp';

const DASHBOARD_ID = 'internal-bi';

const PAGE_PREFIXES: Record<string, string> = {
  overview: 'ov-',
  disease:  'dz-',
  resource: 'rm-',
  service:  'sv-',
  export:   'ex-',
};

const PAGE_TITLES: Record<string, string> = {
  overview: 'Overview',
  disease:  'Disease Surveillance',
  resource: 'Resource Management',
  service:  'Service Performance',
  export:   'Data Export & Query',
  settings: 'Profile & Settings',
};

function bareSection(pageId: string): string {
  if (pageId.startsWith(`${DASHBOARD_ID}/`)) return pageId.slice(DASHBOARD_ID.length + 1);
  return pageId;
}

function fullPageId(section: string): string {
  return `${DASHBOARD_ID}/${section}`;
}

/** Resolve a widget's DOM node. Catalog-runtime widgets (PageRenderer) carry
 *  `data-widget-id`; legacy pages (Export/Settings) use the element `id`. */
function widgetEl(widgetId: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(`[data-widget-id="${CSS.escape(widgetId)}"]`)
      || document.getElementById(widgetId);
}

/** Resolve the actual editable control for a widget. The resolved node
 *  is usually the card `<div data-widget-id>`, which has no `value`; the real
 *  editor lives inside it (the ex-sql-editor's `<textarea data-sql
 *  data-widget-input="ex-sql-editor">`). So: if the element is itself an
 *  input/textarea use it, otherwise reach in for the first hooked control
 *  (`[data-widget-input]` to `textarea[data-sql]` to any `textarea,input`). */
function widgetInputEl(widgetId: string): HTMLInputElement | HTMLTextAreaElement | null {
  const el = widgetEl(widgetId);
  if (!el) return null;
  const isInput = (n: Element | null): n is HTMLInputElement | HTMLTextAreaElement =>
    !!n && 'value' in n && typeof (n as any).value === 'string';
  if (isInput(el)) return el;
  const inner = el.querySelector('[data-widget-input], textarea[data-sql], textarea, input');
  return isInput(inner) ? inner : null;
}

function widgetIdsOnPage(section: string): string[] {
  const prefix = PAGE_PREFIXES[section];
  if (!prefix) return [];
  // Discovered from the DOM at read-time so we reflect what's actually rendered.
  // Catalog-runtime cards expose `data-widget-id`; legacy pages use element ids.
  // Restrict to the section prefix and drop ECharts-internal nested children
  // (generated suffixes like __ECINS).
  const ids = new Set<string>();
  document.querySelectorAll<HTMLElement>('[data-widget-id]').forEach(el => {
    const id = el.dataset.widgetId;
    if (id && id.startsWith(prefix)) ids.add(id);
  });
  document.querySelectorAll<HTMLElement>(`[id^="${prefix}"]`).forEach(el => {
    if (el.id && !el.id.includes('__')) ids.add(el.id);
  });
  return Array.from(ids);
}

function widgetTitle(widgetId: string): string {
  const el = widgetEl(widgetId);
  if (!el) return widgetId;
  // Catalog cards: data-widget-title on the card itself. Legacy: data-title,
  // then the nearest titled ancestor.
  if (el.dataset.widgetTitle) return el.dataset.widgetTitle;
  const dt = el.getAttribute('data-title');
  if (dt) return dt;
  const card = el.closest('[data-widget-title]') as HTMLElement | null;
  if (card) return card.dataset.widgetTitle || widgetId;
  return widgetId;
}

function widgetState(widgetId: string): unknown {
  const app: any = getBiApp();
  const ws = app?.sv?._widgetState?.[widgetId];
  return ws ?? null;
}

/** read_widget payload. `data` is the widget's real last-fetched tabular
 *  data ({columns, rows}), recorded by the catalog runtime (runtime/PageRenderer)
 *  under `window.__nhdwWidgetData` keyed by widget id: this matches the
 *  tool-catalogue contract. Non-tabular / not-yet-fetched widgets carry
 *  data:null and rely on `text_preview` (a trimmed innerText fallback), so the
 *  model still has something to read but never mistakes an unlabelled text blob
 *  for the widget's tabular data. */
function widgetSnapshot(widgetId: string) {
  const el = widgetEl(widgetId);
  if (!el) return null;
  const text = (el.innerText || '').trim().slice(0, 600);
  const data = (typeof window !== 'undefined' && window.__nhdwWidgetData?.[widgetId]) || null;
  return {
    id:           widgetId,
    title:        widgetTitle(widgetId),
    data,
    text_preview: text,
  };
}

function notFound(): { ok: false; error: string } {
  return { ok: false, error: 'widget not on current page' };
}

export type DashboardApi = {
  read_page(): unknown;
  read_widget(args: { widget_id: string }): unknown;
  read_widget_input(args: { widget_id: string }): unknown;
  navigate_to_page(args: { page_id: string }): unknown;
  apply_global_filter(args: { name: string; value: string }): unknown;
  apply_widget_filter(args: { widget_id: string; name: string; value: string }): unknown;
  write_widget_input(args: { widget_id: string; value: string; and_run?: boolean; run?: boolean }): unknown;
  scroll_to_widget(args: { widget_id: string }): unknown;
};

export const dashboardApi: DashboardApi = {
  read_page() {
    const app: any = getBiApp();
    const section = app.active as string;
    const widgets = widgetIdsOnPage(section).map(id => ({
      id, title: widgetTitle(id), state: widgetState(id),
    }));
    return {
      ok:             true,
      dashboard_id:   DASHBOARD_ID,
      page_id:        fullPageId(section),
      page_title:     PAGE_TITLES[section] || section,
      global_filters: { ...app.filters },
      widgets,
    };
  },

  read_widget({ widget_id }) {
    const snap = widgetSnapshot(widget_id);
    if (!snap) return notFound();
    return { ok: true, ...snap };
  },

  read_widget_input({ widget_id }) {
    const el = widgetEl(widget_id);
    if (!el) return notFound();
    // Reach inside the card for the real editor (the SQL textarea has no id):
    // fall back to the card's text when there is no editable control.
    const input = widgetInputEl(widget_id);
    if (input) return { ok: true, value: input.value };
    return { ok: true, value: (el.innerText || '').trim() };
  },

  navigate_to_page({ page_id }) {
    const section = bareSection(page_id);
    if (!(section in PAGE_TITLES)) {
      return { ok: false, error: `unknown page ${page_id}` };
    }
    const app: any = getBiApp();
    app.active = section;
    notify();
    queueMicrotask(() => app.renderActive?.());
    return { ok: true, page_id: fullPageId(section) };
  },

  apply_global_filter({ name, value }) {
    const app: any = getBiApp();
    // The tool-catalogue advertises `region`, but the app's global-filter dims
    // are keyed geography/period/programme. Normalize the region to geography
    // spelling (accept either) before the membership check so the model doesn't
    // loop on a spurious "unknown global filter" error.
    const key = name === 'region' ? 'geography' : name;
    if (!app.filters || !(key in app.filters)) {
      return { ok: false, error: `unknown global filter ${name}` };
    }
    app.setFilter(key, value);
    notify();
    return { ok: true, name: key, value };
  },

  apply_widget_filter({ widget_id, name, value }) {
    const app: any = getBiApp();
    const ws = app?.sv?._widgetState?.[widget_id];
    if (!ws) {
      return { ok: false, error: `widget ${widget_id} has no local state to filter` };
    }
    (ws as any)[name] = value;
    notify();
    queueMicrotask(() => app.renderActive?.());
    return { ok: true, widget_id, name, value };
  },

  write_widget_input({ widget_id, value, and_run, run }) {
    if (!widgetEl(widget_id)) return notFound();
    // The resolved node is the card `<div>` (no `value`); target the editor
    // inside it (the ex-sql-editor's `<textarea data-widget-input>`).
    const input = widgetInputEl(widget_id);
    if (!input) {
      return { ok: false, error: `widget ${widget_id} has no writable input` };
    }
    input.value = value;
    input.dispatchEvent(new Event('input',  { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    // Optional: also click the editor's Run button (ex-sql-editor: [data-run]).
    let ran = false;
    if (and_run || run) {
      const card = widgetEl(widget_id);
      const runBtn = card?.querySelector<HTMLButtonElement>('[data-run]')
                  || card?.querySelector<HTMLButtonElement>('button');
      if (runBtn) { runBtn.click(); ran = true; }
    }
    return { ok: true, widget_id, ran };
  },

  scroll_to_widget({ widget_id }) {
    const el = widgetEl(widget_id);
    if (!el) return notFound();
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('chatbot-highlight');
    setTimeout(() => el.classList.remove('chatbot-highlight'), 1400);
    return { ok: true, widget_id };
  },
};

declare global {
  interface Window {
    __nhdwDashboard?: DashboardApi;
    /** read_widget registry: last-fetched {columns, rows} per widget id,
     *  populated by the catalog runtime (runtime/PageRenderer.recordWidgetData). */
    __nhdwWidgetData?: Record<string, { columns: string[]; rows: Record<string, any>[] } | null>;
  }
}

export function installDashboardApi(): void {
  if (typeof window !== 'undefined') {
    window.__nhdwDashboard = dashboardApi;
  }
}
