/** ws-projects-table: the left "Project catalogue" master list of the Projects
 *  master-detail page.
 *
 *   GET /gw/projects?scope=all: projects VISIBLE to the caller (own ∪ public
 *   ∪ group-shared); the gateway's visibility clause scopes the payload, so
 *   another user's private project never reaches this widget.
 *
 * A narrow card: header + "New project", a search box, a segmented All / My
 * projects / Public tab row with a visibility filter + sort dropdown, a vertical
 * list of compact project ROWS (visibility icon · name · one-line description ·
 * owner avatar · updated date · chevron), and a "Showing X-Y of N" footer with a
 * numbered pager. Clicking a row publishes the `ws_project` bus dimension: the
 * detail (with its Visibility control + Project files) reacts. "New project"
 * opens the project-create modal. Data fetches are unchanged from the grid era. */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import {
  listProjects, visibilityIcon, visibilityPillClass,
  projectGradient, fmtDate, type ProjectRow,
} from '../../api/projects';
import { esc } from '../_shared/helpers';

const PER_PAGE = 5;

/** Two-letter monogram from an owner name / project name (first + last word). */
function initials(name: string): string {
  const words = String(name || '').trim().split(/\s+/).filter(Boolean);
  if (!words.length) return '·';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

function row(p: ProjectRow, selected: boolean): string {
  const owner = p.owner_username || 'you';
  return `
    <button class="prj-row${selected ? ' active' : ''}" data-pid="${esc(p.id)}"
            title="Open this project">
      <span class="prj-row-vis ${esc(visibilityPillClass(p.visibility))}" aria-hidden="true">
        <svg class="ico"><use href="#${esc(visibilityIcon(p.visibility))}"/></svg>
      </span>
      <span class="prj-row-body">
        <span class="prj-row-name">${esc(p.name)}</span>
        <span class="prj-row-desc">${esc(p.description || 'No description yet.')}</span>
        <span class="prj-row-meta">
          <span class="prj-avatar" style="background:${projectGradient(p.owner_kc_sub || p.id)}">${esc(initials(owner))}</span>
          <span class="prj-row-owner">${esc(owner)}</span>
          <span class="prj-row-dot">·</span>
          <span class="prj-row-updated">Updated ${esc(fmtDate(p.updated_at))}</span>
        </span>
      </span>
      <svg class="ico prj-row-chev"><use href="#i-chevron-right"/></svg>
    </button>`;
}

async function draw(ctx: WidgetCtx) {
  let projects: ProjectRow[] | null = null;
  let loadErr = '';
  let myKcSub: string | null = null;
  try {
    projects = await listProjects({ scope: 'all' });
  } catch (e: any) { loadErr = e?.message || String(e); }
  try {
    const me = await authFetch('/auth/me').then(r => r.json());
    if (me?.kc_sub) myKcSub = me.kc_sub;
  } catch { /* fall back to the private-is-mine invariant */ }

  if (projects === null) { ctx.empty(`Could not load projects: ${loadErr}`); return; }

  // "My" = anything I own; a private project is only ever returned to its owner,
  // so it's mine even if /auth/me hasn't resolved a sub.
  const isMine = (p: ProjectRow) =>
    (!!myKcSub && p.owner_kc_sub === myKcSub) || p.visibility === 'private';

  const state = { q: '', tab: 'all', vis: 'all', sort: 'updated', page: 1 };

  ctx.el.innerHTML = `
    <div class="prj-cat">
      <div class="prj-cat-hd">
        <h3 class="prj-cat-title">Project catalogue</h3>
        <button class="btn btn-sm btn-primary" data-act="new">
          <svg class="ico ico-sm"><use href="#i-plus"/></svg> New project</button>
      </div>
      <span class="art-search prj-cat-search">
        <svg class="ico"><use href="#i-search"/></svg>
        <input class="ws-flt-q" placeholder="Search projects" aria-label="Search projects">
      </span>
      <div class="prj-cat-filters">
        <div class="prj-seg" role="tablist">
          <button type="button" class="active" data-tab="all">All</button>
          <button type="button" data-tab="mine">My projects</button>
          <button type="button" data-tab="public">Public</button>
        </div>
        <span class="prj-cat-selwrap">
          <svg class="ico"><use href="#i-shield"/></svg>
          <select class="ws-flt-vis" aria-label="Filter by visibility">
            <option value="all">All visibility</option>
            <option value="private">Private</option>
            <option value="group">Group</option>
            <option value="public">Public</option>
          </select>
        </span>
        <span class="prj-cat-selwrap">
          <select class="ws-flt-sort" aria-label="Sort projects">
            <option value="updated">Last updated</option>
            <option value="created">Recently created</option>
            <option value="name">Name A to Z</option>
          </select>
        </span>
      </div>
      <div class="prj-cat-list ws-prj-list"></div>
      <div class="prj-cat-foot">
        <span class="prj-cat-count"></span>
        <div class="prj-pager"></div>
      </div>
    </div>`;

  const listEl = ctx.el.querySelector<HTMLElement>('.ws-prj-list')!;
  const countEl = ctx.el.querySelector<HTMLElement>('.prj-cat-count')!;
  const pagerEl = ctx.el.querySelector<HTMLElement>('.prj-pager')!;

  const compute = (): ProjectRow[] => {
    const term = state.q.trim().toLowerCase();
    let rows = projects!.filter(p => {
      if (term && !(`${p.name} ${p.description || ''} ${p.owner_username || ''}`
        .toLowerCase().includes(term))) return false;
      if (state.tab === 'mine' && !isMine(p)) return false;
      if (state.tab === 'public' && p.visibility !== 'public') return false;
      if (state.vis !== 'all' && p.visibility !== state.vis) return false;
      return true;
    });
    rows = rows.slice().sort((a, b) => {
      if (state.sort === 'name') return a.name.localeCompare(b.name);
      const key = state.sort === 'created' ? 'created_at' : 'updated_at';
      return String(b[key] || '').localeCompare(String(a[key] || ''));
    });
    return rows;
  };

  const paint = () => {
    const rows = compute();
    const total = rows.length;
    const pages = Math.max(1, Math.ceil(total / PER_PAGE));
    if (state.page > pages) state.page = pages;
    const start = (state.page - 1) * PER_PAGE;
    const pageRows = rows.slice(start, start + PER_PAGE);
    const selected = ctx.bus.get().ws_project || '';

    // Keep the segmented tabs / selects in sync with state (survive re-paints).
    ctx.el.querySelectorAll<HTMLButtonElement>('.prj-seg button').forEach(b =>
      b.classList.toggle('active', b.dataset.tab === state.tab));

    if (projects!.length === 0) {
      listEl.innerHTML = `
        <div class="art-empty">
          <svg class="ico"><use href="#i-folder"/></svg>
          <h4>No projects yet</h4>
          <p>Create one to get a private file space in the warehouse and an
          experiment for its model runs.</p>
        </div>`;
    } else if (total === 0) {
      listEl.innerHTML = `
        <div class="art-empty">
          <svg class="ico"><use href="#i-search"/></svg>
          <h4>No projects match</h4>
          <p>Nothing matches the current search and filters.</p>
        </div>`;
    } else {
      listEl.innerHTML = pageRows.map(p => row(p, p.id === selected)).join('');
      listEl.querySelectorAll<HTMLButtonElement>('button.prj-row').forEach(btn => {
        btn.addEventListener('click', () => {
          ctx.bus.set('ws_project', btn.dataset.pid!);
          listEl.querySelectorAll('button.prj-row').forEach(x =>
            x.classList.toggle('active', x === btn));
          ctx.host.focusWidget('ws-project-detail');
        });
      });
    }

    // Footer count + numbered pager.
    if (total === 0) { countEl.textContent = 'No projects'; pagerEl.innerHTML = ''; return; }
    const from = start + 1, to = Math.min(start + PER_PAGE, total);
    countEl.textContent = `Showing ${from} to ${to} of ${total} project${total === 1 ? '' : 's'}`;
    if (pages <= 1) { pagerEl.innerHTML = ''; return; }
    // Windowed page numbers (max 5) around the current page.
    let lo = Math.max(1, state.page - 2), hi = Math.min(pages, lo + 4);
    lo = Math.max(1, hi - 4);
    let btns = `<button class="prj-pg" data-pg="${state.page - 1}" ${state.page === 1 ? 'disabled' : ''} aria-label="Previous"><svg class="ico"><use href="#i-arrow-right"/></svg></button>`;
    for (let i = lo; i <= hi; i++) {
      btns += `<button class="prj-pg${i === state.page ? ' active' : ''}" data-pg="${i}">${i}</button>`;
    }
    btns += `<button class="prj-pg" data-pg="${state.page + 1}" ${state.page === pages ? 'disabled' : ''} aria-label="Next"><svg class="ico"><use href="#i-arrow-right"/></svg></button>`;
    pagerEl.innerHTML = btns;
    pagerEl.querySelectorAll<HTMLButtonElement>('button.prj-pg').forEach(b =>
      b.addEventListener('click', () => {
        const pg = Number(b.dataset.pg);
        if (pg >= 1 && pg <= pages && pg !== state.page) { state.page = pg; paint(); }
      }));
  };

  // Auto-select a project so the detail pane is populated on arrival (matches
  // the reference: a row is selected and its detail shown). Prefer the user's
  // own first project so they land on their own work with the lab enabled,
  // falling back to the first visible project otherwise.
  if (!ctx.bus.get().ws_project) {
    const sorted = compute();
    const pick = sorted.find(isMine) || sorted[0];
    if (pick) ctx.bus.set('ws_project', pick.id);
  }

  // wire the toolbar
  ctx.el.querySelector<HTMLButtonElement>('button[data-act="new"]')!
    .addEventListener('click', () => {
      ctx.host.openModal('project-create', {
        onDone: async (created?: { id?: string }) => {
          try { projects = await listProjects({ scope: 'all' }); } catch { /* keep old list */ }
          if (created?.id) ctx.bus.set('ws_project', created.id);   // open the fresh project
          state.page = 1;
          paint();
        },
      });
    });
  ctx.el.querySelector<HTMLInputElement>('.ws-flt-q')!
    .addEventListener('input', (e) => {
      state.q = (e.currentTarget as HTMLInputElement).value;
      state.page = 1; paint();
    });
  ctx.el.querySelectorAll<HTMLButtonElement>('.prj-seg button').forEach(b =>
    b.addEventListener('click', () => {
      state.tab = b.dataset.tab!; state.page = 1; paint();
    }));
  ctx.el.querySelector<HTMLSelectElement>('.ws-flt-vis')!
    .addEventListener('change', (e) => {
      state.vis = (e.currentTarget as HTMLSelectElement).value; state.page = 1; paint();
    });
  ctx.el.querySelector<HTMLSelectElement>('.ws-flt-sort')!
    .addEventListener('change', (e) => {
      state.sort = (e.currentTarget as HTMLSelectElement).value; state.page = 1; paint();
    });

  paint();
}

const def: WidgetDef = {
  id: 'ws-projects-table',
  reactsTo: [],   // it PUBLISHES ws_project
  render(ctx) { return draw(ctx); },
};
export default def;
