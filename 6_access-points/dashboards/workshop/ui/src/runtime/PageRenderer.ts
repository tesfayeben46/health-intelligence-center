/**
 * PageRenderer: draws a page entirely from the catalog (gateway spec). The
 * shell does not know which sections or widgets exist; it renders whatever the
 * per-role spec hands it:
 *
 *   page.sections[]: a titled band each (eyebrow/title/description from catalog)
 *   section.widgets[]: a CSS grid of `section.columns` tracks; each widget card
 *                       spans `widget.col_span`; grid-auto-flow: dense so that
 *                       when a permission removes a neighbour, the survivors
 *                       reflow to fill the gap.
 *
 * Each widget is mounted by looking its id up in the build-time registry and
 * calling its render(ctx). Data is real or empty (dataHook), never synthetic.
 */
import type { ResolvedPage, ResolvedSection, ResolvedWidget } from '../api/spec';
import type { WidgetCtx, WidgetData, WidgetDef } from './types';
import { getWidgetDef } from './registry';
import { resolveTheme } from './theme';
import { filterBus } from './filterBus';
import { fetchWidgetData, fetchEntity } from './dataHook';
import { host } from './host';

/** Standard empty state: one visual language for "no real data" everywhere.
 *  Exported so the app-shell (detail views / modals) shows the same empty. */
export function emptyState(el: HTMLElement, message?: string): void {
  el.innerHTML = `
    <div class="widget-empty">
      <svg class="ico" aria-hidden="true"><use href="#i-database"/></svg>
      <span>${message || 'No data available for the current scope.'}</span>
    </div>`;
}

/** Mount a single widget into its card body. Returns a teardown that disposes
 *  the widget's render + its filter subscription. */
export function mountWidget(
  widget: ResolvedWidget,
  section: ResolvedSection | undefined,
  page: ResolvedPage,
  body: HTMLElement,
): () => void {
  let cleanup: void | (() => void);
  let disposed = false;
  let gen = 0;   // draw generation: guards overlapping async re-renders
  let unsub: () => void = () => {};

  const draw = (d: WidgetDef) => {
    const myGen = ++gen;
    // dispose previous render (chart instances, listeners) before re-render
    try { if (typeof cleanup === 'function') cleanup(); } catch { /* noop */ }
    cleanup = undefined;
    // Each draw renders into its own slot; clearing `body` detaches any prior
    // slot, so a stale async render (fetchData still in flight when a filter
    // change fires a new draw) appends into a detached slot: never the page.
    body.innerHTML = '';
    const slot = document.createElement('div');
    slot.style.height = '100%';
    body.appendChild(slot);

    const ctx: WidgetCtx = {
      el: slot,
      widget,
      section,
      page,
      theme: resolveTheme(),
      filters: filterBus.get(),
      bus: filterBus,
      host,
      fetchData: (params) => fetchWidgetData(widget.data_url, params),
      fetchEntity: (path, params) => fetchEntity(path, params),
      empty: (m) => emptyState(slot, m),
    };
    const result = d.render(ctx);
    if (result instanceof Promise) {
      result.then(c => {
        if (myGen !== gen) { if (typeof c === 'function') { try { c(); } catch { /* noop */ } } return; }
        if (!disposed) cleanup = c; else if (typeof c === 'function') c();
      });
    } else {
      cleanup = result;
    }
  };

  // Start rendering with a resolved WidgetDef + wire the reacts_to re-render.
  const start = (d: WidgetDef) => {
    if (disposed) return;
    draw(d);
    const reacts = (d.reactsTo && d.reactsTo.length) ? d.reactsTo : (widget.reacts_to || []);
    unsub = reacts.length ? filterBus.subscribe(reacts, () => { if (!disposed) draw(d); }) : () => {};
  };

  const def = getWidgetDef(widget.id);
  if (def) {
    // In-tree widget (build-time registry): the common path.
    start(def);
  } else {
    body.innerHTML =
      `<div class="widget-empty">No renderer registered for <code>${widget.id}</code></div>`;
  }

  return () => {
    disposed = true;
    unsub();
    try { if (typeof cleanup === 'function') cleanup(); } catch { /* noop */ }
  };
}

/** Render one section band (header + reflowing widget grid). */
function renderSection(section: ResolvedSection, page: ResolvedPage, mount: HTMLElement, teardowns: Array<() => void>): void {
  const widgets = (section.widgets || []).filter(w => !String(w.placement || '').startsWith('chrome.'));
  if (!widgets.length) return;   // gateway already drops empty sections; belt-and-braces

  const band = document.createElement('section');
  band.className = 'page-section';
  band.dataset.sectionId = section.id;

  // Header: eyebrow + title + description straight from the catalog. On the
  // overview digest, a section named after another page gets a "full page" jump.
  if (section.title || section.eyebrow) {
    const hdr = document.createElement('div');
    hdr.className = 'ps-hdr';
    const m = /\/overview\/([a-z-]+)$/.exec(section.id);
    const targetPage = (page.id.endsWith('/overview') && m && m[1] !== 'national')
      ? page.id.replace(/\/overview$/, `/${m[1]}`) : null;
    hdr.innerHTML = `
      <div>
        ${section.eyebrow ? `<div class="ps-eyebrow">${section.eyebrow}</div>` : ''}
        ${section.title ? `<h3 class="ps-title">${section.title}</h3>` : ''}
        ${section.description ? `<p class="ps-sub">${section.description}</p>` : ''}
      </div>
      ${targetPage ? `<button class="ps-fullpage" type="button">View full page ↗</button>` : ''}`;
    if (targetPage) {
      const btn = hdr.querySelector<HTMLButtonElement>('.ps-fullpage');
      if (btn) btn.onclick = () => host.navigateToPage(targetPage);
    }
    band.appendChild(hdr);
  }

  // Body: a grid of `columns` tracks; widgets span col_span; dense reflow.
  const cols = Math.max(1, section.columns || 12);
  const grid = document.createElement('div');
  grid.className = 'ps-grid';
  grid.style.display = 'grid';
  grid.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
  grid.style.gridAutoFlow = section.dense === false ? 'row' : 'row dense';
  grid.style.gap = '1rem';
  // Cards hug their content (start), not stretch, so a taller sibling does not
  // force a shorter card to grow and leave a big internal white void. Ragged
  // bottoms across a row read as natural cards, not as broken empty space.
  grid.style.alignItems = 'start';
  band.appendChild(grid);

  // Permission REFLOW-to-fill: when a role removes neighbours, the survivors of
  // each row grow to consume the leftover tracks (grid-auto-flow: dense only
  // fills positional gaps: it never widens a widget). Pack widgets greedily
  // into rows using their declared col_span, then distribute each row's leftover
  // columns round-robin across that row's survivors so no trailing gap remains.
  const declared = widgets.map(w => Math.min(cols, Math.max(1, w.col_span || Math.round(cols / 2))));
  const effSpan = declared.slice();
  {
    let row: number[] = [], sum = 0;
    const flushRow = () => {
      let rem = cols - sum;
      for (let k = 0; rem > 0 && row.length; k++, rem--) effSpan[row[k % row.length]]++;
      row = []; sum = 0;
    };
    declared.forEach((s, i) => {
      if (sum + s > cols && row.length) flushRow();
      row.push(i); sum += s;
    });
    flushRow();
  }

  widgets.forEach((w, i) => {
    const span = effSpan[i];
    const card = document.createElement('div');
    card.className = 'card card-elev widget-card';
    card.dataset.widgetId = w.id;
    if (w.title) card.dataset.widgetTitle = w.title;   // read by the AI assistant (dashboardApi)
    card.style.gridColumn = `span ${span}`;
    if (w.row_span && w.row_span > 1) card.style.gridRow = `span ${w.row_span}`;
    card.style.display = 'flex';
    card.style.flexDirection = 'column';
    card.style.minWidth = '0';

    // Card chrome. The catalog title is an admin-facing description
    // ("Workshop · Model workbench (detail + card + versions + predict runner)");
    // rendered verbatim it would stack a third heading under the section
    // eyebrow and title. Show a clean short label, and only
    // when it actually adds information: a section with multiple widgets needs
    // per-card labels; a single-widget section is already named by its header,
    // so the card stays chrome-free (the widget owns its body). `data-widget-
    // title` keeps the full catalog title for the assistant's DOM reader.
    const cleanTitle = (w.title || '')
      .replace(/^Workshop\s*[·:-]\s*/i, '')
      .replace(/\s*\([^)]*\)\s*$/, '')
      .trim();
    const sectionTitle = (section.title || '').trim().toLowerCase();
    const showTitle = cleanTitle
      && widgets.length > 1
      && cleanTitle.toLowerCase() !== sectionTitle;
    if (showTitle) {
      const h = document.createElement('div');
      h.className = 'widget-hd';
      h.innerHTML = `<h4 class="widget-title">${cleanTitle}</h4>`;
      card.appendChild(h);
    }

    const bodyEl = document.createElement('div');
    bodyEl.className = 'widget-body';
    bodyEl.style.flex = '1';
    bodyEl.style.minHeight = '0';
    card.appendChild(bodyEl);

    grid.appendChild(card);
    teardowns.push(mountWidget(w, section, page, bodyEl));
  });

  mount.appendChild(band);
}

/** Render a whole page from its resolved spec. Returns a teardown that disposes
 *  every mounted widget (call before re-rendering / leaving the page). */
export function renderPage(page: ResolvedPage, mount: HTMLElement): () => void {
  mount.innerHTML = '';
  const teardowns: Array<() => void> = [];
  const sections = [...(page.sections || [])].sort((a, b) => a.ordering - b.ordering);
  if (!sections.length) {
    mount.innerHTML = `<div class="widget-empty" style="padding:3rem">No sections granted on this page for your role.</div>`;
    return () => {};
  }
  for (const s of sections) renderSection(s, page, mount, teardowns);
  return () => { for (const t of teardowns) { try { t(); } catch { /* noop */ } } };
}
