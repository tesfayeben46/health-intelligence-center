/** ws-datasets-table: the datasets catalog. Datasets are an independent
 *  entity: they carry their own tier ACL and are
 *  many-to-many linked to projects.
 *
 *   GET /gw/datasets (+ q, tier, owner=me, project_id=<uuid>): the route's
 *   visibility clause scopes what the caller may see; each row folds in its
 *   `linked_projects` (the M:N connections). Filters: access level
 *   (My = owner=me · Group = tier=shared · Public = tier=public) + project
 *   dropdown (link-table filter) + debounced search.
 *
 * The catalog can pin the tier surface per role (widget filter
 * `artifact_tier`): pinned tiers remove the disallowed segments and cap the
 * rows client-side (the gateway still enforces server-side).
 *
 * Per-row "Details" opens the dataset-detail modal (versions, metrics, like
 * toggle, real download, project attach/detach). reactsTo `ws_datasets` so a
 * successful builder run refreshes the table. */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import { esc, errText, unwrapList, fmtCount, fmtDate, tierLabel } from '../_shared/helpers';

type LinkedProject = { id: string; name: string };
type DatasetRow = {
  id: string; title: string; description: string | null;
  tier: string; owner_username: string | null; updated_at: string;
  downloads: number | null; views: number | null; likes: number | null;
  latest_version: number | null; linked_projects: LinkedProject[];
};
type ProjectRow = { id: string; name: string };

type Scope = 'all' | 'mine' | 'shared' | 'public';
const SCOPES: { key: Scope; label: string; tier: string | null }[] = [
  { key: 'all', label: 'All', tier: null },
  { key: 'mine', label: 'My', tier: null },          // owner=me
  { key: 'shared', label: 'Group', tier: 'shared' },
  { key: 'public', label: 'Public', tier: 'public' },
];

async function draw(ctx: WidgetCtx) {
  const { theme } = ctx;

  // Spec-pinned tier subset (allowed_values): null/absent = wildcard.
  const tierFilter = (ctx.widget.local_filters || []).find(f => f.id === 'artifact_tier');
  const allowedTiers: string[] | null = tierFilter?.allowed_values ?? null;
  const tierAllowed = (t: string) => !allowedTiers || allowedTiers.includes(t);

  const scopes = SCOPES.filter(s => {
    if (!allowedTiers) return true;
    if (s.key === 'all') return true;
    if (s.key === 'mine') return true;   // owner=me: still capped to allowed tiers below
    return allowedTiers.includes(s.tier!);
  });

  let projects: ProjectRow[] = [];
  try {
    const rP = await authFetch('/gw/projects');
    if (rP.ok) projects = unwrapList<ProjectRow>(await rP.json(), 'projects');
  } catch { /* project filter degrades to "all projects" */ }

  const state = { scope: 'all' as Scope, project: '', q: '' };

  ctx.el.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:.7rem">
      <div style="display:flex;gap:.6rem;flex-wrap:wrap;align-items:center">
        <div class="art-scope" role="group" aria-label="Access level">
          ${scopes.map(s => `<button type="button" class="art-scope-btn${s.key === 'all' ? ' active' : ''}" data-scope="${s.key}">${esc(s.label)}</button>`).join('')}
        </div>
        <select class="art-sort ws-flt-project" aria-label="Project">
          <option value="">All projects</option>
          ${projects.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}
        </select>
        <span class="art-search" style="flex:1;min-width:200px;margin:0">
          <svg class="ico"><use href="#i-search"/></svg>
          <input class="ws-flt-q" placeholder="Search datasets by title or description" aria-label="Search datasets">
        </span>
      </div>
      <div class="ws-tbl-out"><p class="muted" style="font-size:.85rem">Loading datasets…</p></div>
    </div>`;

  const out = ctx.el.querySelector<HTMLElement>('.ws-tbl-out')!;

  const load = async () => {
    out.innerHTML = `<p class="muted" style="font-size:.85rem">Loading datasets…</p>`;
    const qs = new URLSearchParams({ limit: '100' });
    const scope = SCOPES.find(s => s.key === state.scope)!;
    if (scope.key === 'mine') qs.set('owner', 'me');
    else if (scope.tier) qs.set('tier', scope.tier);
    if (state.project) qs.set('project_id', state.project);
    if (state.q) qs.set('q', state.q);
    let items: DatasetRow[] = [];
    let total = 0;
    try {
      const r = await authFetch(`/gw/datasets?${qs}`);
      if (!r.ok) {
        out.innerHTML = `<div class="widget-empty">Could not load datasets: ${esc(await errText(r))}</div>`;
        return;
      }
      const j = await r.json();
      items = unwrapList<DatasetRow>(j);
      total = Number(j?.count ?? items.length);
    } catch {
      out.innerHTML = `<div class="widget-empty">Could not load datasets.</div>`;
      return;
    }
    // Defence-in-depth: cap to the spec-pinned tier subset.
    if (allowedTiers) items = items.filter(a => tierAllowed(a.tier));

    if (!items.length) {
      out.innerHTML = `<div class="widget-empty">
        <svg class="ico" aria-hidden="true"><use href="#i-database"/></svg>
        <span>No datasets match the current filters${state.q ? ` (search: “${esc(state.q)}”)` : ''}.</span></div>`;
      return;
    }

    out.innerHTML = `
      <div class="scrollable" style="overflow-x:auto">
        <table class="dash" style="margin:0;width:100%">
          <thead><tr>
            <th>Dataset</th><th>Projects</th><th>Tier</th><th>Owner</th>
            <th>Updated</th><th style="text-align:right" title="downloads · views · likes">Activity</th><th>Version</th><th></th>
          </tr></thead>
          <tbody>
            ${items.map(a => `
              <tr>
                <td>
                  <strong>${esc(a.title)}</strong>
                  ${a.description ? `<div style="font-size:.76rem;color:${theme.ink[2]};max-width:44ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(a.description)}</div>` : ''}
                </td>
                <td>${(a.linked_projects && a.linked_projects.length)
                  ? `<span style="display:flex;flex-wrap:wrap;gap:.25rem">${a.linked_projects.map(p => `<span class="pill neutral" title="${esc(p.name)}">${esc(p.name)}</span>`).join('')}</span>`
                  : '<span class="muted" title="Independent, not linked to any project">unlinked</span>'}</td>
                <td><span class="art-tier ${esc(a.tier)}">${esc(tierLabel(a.tier))}</span></td>
                <td>${esc(a.owner_username || 'n/a')}</td>
                <td>${esc(fmtDate(a.updated_at))}</td>
                <td style="text-align:right;font-variant-numeric:tabular-nums"
                    title="${esc(fmtCount(a.downloads ?? 0))} downloads · ${esc(fmtCount(a.views ?? 0))} views · ${esc(fmtCount(a.likes ?? 0))} likes"
                >${esc(fmtCount(a.downloads ?? 0))} · ${esc(fmtCount(a.views ?? 0))} · ${esc(fmtCount(a.likes ?? 0))}</td>
                <td>${a.latest_version != null ? `<code>v${esc(a.latest_version)}</code>` : 'n/a'}</td>
                <td><button class="btn btn-sm btn-ghost" data-aid="${esc(a.id)}">Details</button></td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>
      <p class="muted" style="font-size:.76rem;margin:.4rem 0 0">${esc(String(total))} dataset${total === 1 ? '' : 's'} visible to you${allowedTiers ? ` · tier surface capped to ${esc(allowedTiers.join(', '))} for your role` : ''}.</p>`;

    out.querySelectorAll<HTMLButtonElement>('button[data-aid]').forEach(btn => {
      btn.addEventListener('click', () =>
        ctx.host.openModal('dataset-detail', { artifactId: btn.dataset.aid, onDone: () => { void load(); } }));
    });
  };

  ctx.el.querySelectorAll<HTMLButtonElement>('button[data-scope]').forEach(btn => {
    btn.addEventListener('click', () => {
      state.scope = btn.dataset.scope as Scope;
      ctx.el.querySelectorAll('button[data-scope]').forEach(b =>
        b.classList.toggle('active', b === btn));
      void load();
    });
  });
  ctx.el.querySelector<HTMLSelectElement>('.ws-flt-project')!
    .addEventListener('change', (e) => {
      state.project = (e.currentTarget as HTMLSelectElement).value;
      void load();
    });
  let debounce: number | undefined;
  ctx.el.querySelector<HTMLInputElement>('.ws-flt-q')!
    .addEventListener('input', (e) => {
      const v = (e.currentTarget as HTMLInputElement).value.trim();
      window.clearTimeout(debounce);
      debounce = window.setTimeout(() => { state.q = v; void load(); }, 250);
    });

  await load();
  return () => { window.clearTimeout(debounce); };
}

const def: WidgetDef = {
  id: 'ws-datasets-table',
  reactsTo: ['ws_datasets'],   // dataset-builder pokes this after a generate
  render(ctx) { return draw(ctx); },
};
export default def;
