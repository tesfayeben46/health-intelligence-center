/** ws-project-detail: the right (wide) column of the Projects master-detail
 *  page: status pills, a
 *  large serif name, description, a meta row (owner · ID+copy · created · role),
 *  the action toolbar, then a divider and two sub-columns: the Visibility
 *  radio-card control and the Project-files tree (ws-project-files composed
 *  in-place, since the catalog grid can't nest a detail column).
 *  reactsTo `ws_project` (published by ws-projects-table).
 *
 *   GET   /gw/projects/{id}        ACL-checked detail
 *   PATCH /gw/projects/{id}        owner-only visibility change; choosing
 *                                  "group" requires a group you belong to
 *                                  (picker from GET /gw/groups)
 *   POST  /gw/workspace/spawn      {project_id}: open the returned url;
 *                                  shown disabled when the hub isn't
 *                                  configured (GET /gw/workspace/status)
 *
 * Every action + its gating is unchanged: only presentation/layout changed. */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import {
  getProject, updateProject,
  visibilityLabel, visibilityIcon, visibilityPillClass, visibilityHint,
  statusLabel, statusPillClass, reviewStatusLabel, reviewStatusPillClass,
  projectGradient, fmtDate,
  type ProjectRow, type ProjectVisibility,
} from '../../api/projects';
import { listGroups, type GroupRow } from '../../api/groups';
import { esc, errText, toast } from '../_shared/helpers';
import { emptyState } from '../../runtime/PageRenderer';
import wsProjectFiles from '../ws-project-files/widget';

const VISIBILITIES: ProjectVisibility[] = ['private', 'group', 'public'];

/** Two-letter monogram from an owner name (first + last word initials). */
function initials(name: string): string {
  const words = String(name || '').trim().split(/\s+/).filter(Boolean);
  if (!words.length) return '·';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

async function draw(ctx: WidgetCtx): Promise<void | (() => void)> {
  const { theme } = ctx;
  const projectId = ctx.filters.ws_project || '';
  if (!projectId) {
    ctx.empty('Select a project in the catalogue on the left to see its detail, files and visibility.');
    return;
  }

  let project: ProjectRow | null = null;
  let loadErr = '';
  let myKcSub: string | null = null;
  try { project = await getProject(projectId); }
  catch (e: any) { loadErr = e?.message || String(e); }
  try {
    const me = await authFetch('/auth/me').then(r => r.json());
    if (me?.kc_sub) myKcSub = me.kc_sub;
  } catch { /* isOwner stays conservative */ }

  if (!project) { ctx.empty(`Could not load this project: ${loadErr}`); return; }

  // Hub availability decides whether the open-in-lab button is enabled.
  let hubReason: string | null = null;   // non-null means hub unusable, reason verbatim
  let hubRunningUrl: string | null = null;
  try {
    // Per-project status: is this project's named lab running?
    const r = await authFetch(`/gw/workspace/status?project_id=${encodeURIComponent(projectId)}`);
    if (r.ok) {
      const st = await r.json();
      if (st.running) hubRunningUrl = st.url || st.server || null;
      else if (/not configured/i.test(st.reason || '')) hubReason = st.reason;
    }
  } catch { /* leave the button enabled; spawn errors surface on click */ }

  const isOwner = (!!myKcSub && project.owner_kc_sub === myKcSub) || project.visibility === 'private';
  const p = project;
  // Who may open a lab: the owner, or a member of a GROUP project (group projects
  // share files by design: seeing one implies membership). A PUBLIC project
  // you don't own is not openable (a lab writes to the project's shared files); you
  // CLONE it into a private copy instead. The gateway enforces this too (403).
  const canOpenLab = isOwner || p.visibility === 'group';

  // public-review state
  //   draft / changes_requested / rejected: submittable
  //   in_review: disabled "In review"
  //   approved: published (a "Public" badge, no button)
  const rs = (p.review_status as string) || 'draft';
  const canSubmitReview = isOwner && (rs === 'draft' || rs === 'changes_requested' || rs === 'rejected');
  const submitLabel = rs === 'changes_requested' || rs === 'rejected' ? 'Re-submit for review' : 'Submit for review';

  // "Your role" on this project: derived from ownership + visibility (all real,
  // no new fetch): owner, a group member sharing files, or a public viewer.
  const roleLabel = isOwner ? 'Owner' : (p.visibility === 'group' ? 'Member' : 'Viewer');
  const roleIcon  = isOwner ? 'i-user' : (p.visibility === 'group' ? 'i-users' : 'i-eye');
  const ownerName = p.owner_username || (isOwner ? 'you' : p.owner_kc_sub);

  ctx.el.innerHTML = `
    <div class="prj-detail2">
      <div class="prj-d-pills">
        <span class="prj-vis-badge ${esc(visibilityPillClass(p.visibility))}">
          <svg class="ico ico-sm"><use href="#${esc(visibilityIcon(p.visibility))}"/></svg>
          ${esc(visibilityLabel(p.visibility))}
        </span>
        <span class="pill ${esc(statusPillClass(p.status))}">${esc(statusLabel(p.status))}</span>
        <span class="pill ${esc(reviewStatusPillClass(rs))}">${esc(reviewStatusLabel(rs))}</span>
      </div>

      <h2 class="prj-d-name">${esc(p.name)}</h2>
      ${p.description ? `<p class="prj-d-desc">${esc(p.description)}</p>` : ''}

      <div class="prj-d-meta">
        <span class="prj-d-meta-item">
          <span class="prj-avatar" style="background:${projectGradient(p.owner_kc_sub || p.id)}">${esc(initials(ownerName))}</span>
          <strong>${esc(ownerName)}</strong>
        </span>
        <span class="prj-d-meta-sep"></span>
        <span class="prj-d-meta-item">
          <span class="prj-d-meta-lbl">ID</span>
          <code class="prj-d-id">${esc(p.slug)}</code>
          <button class="prj-copy" data-act="copy-id" title="Copy project ID" aria-label="Copy project ID">
            <svg class="ico ico-sm"><use href="#i-copy"/></svg></button>
        </span>
        <span class="prj-d-meta-sep"></span>
        <span class="prj-d-meta-item">
          <svg class="ico ico-sm"><use href="#i-calendar"/></svg> Created ${esc(fmtDate(p.created_at))}
        </span>
        <span class="prj-d-meta-sep"></span>
        <span class="prj-d-meta-item prj-d-role" title="Your role on this project">
          <svg class="ico ico-sm"><use href="#${roleIcon}"/></svg> ${esc(roleLabel)}
        </span>
      </div>

      <div class="prj-actions">
        ${!canOpenLab
          ? `<button class="btn btn-sm btn-ghost" disabled aria-disabled="true"
               title="Public project you don't own. Clone it to open a lab on your own copy.">
               <svg class="ico ico-sm"><use href="#i-external"/></svg> Open in JupyterLab</button>
             <button class="btn btn-sm btn-primary" data-act="clone">
               <svg class="ico ico-sm"><use href="#i-plus"/></svg> Clone to my workspace</button>
             <span class="prj-actions-hint muted">A lab writes to the project’s shared files, so a public project can only be opened by its owner. Clone it to work on your own private copy.</span>`
          : hubReason
          ? `<button class="btn btn-sm btn-ghost" disabled aria-disabled="true" title="${esc(hubReason)}">
               <svg class="ico ico-sm"><use href="#i-external"/></svg> Open in JupyterLab</button>
             <span class="prj-actions-hint muted">${esc(hubReason)}</span>`
          : `<button class="btn btn-sm btn-primary" data-act="open-lab">
               <svg class="ico ico-sm"><use href="#i-external"/></svg> Open in JupyterLab</button>
             <span class="prj-actions-hint muted">${hubRunningUrl
               ? 'Your lab is running; opens it on this project.'
               : 'Spawns your lab pod on this project’s file space.'}</span>`}
        <button class="btn btn-sm btn-ghost" data-act="link-datasets">
          <svg class="ico ico-sm"><use href="#i-database"/></svg> Link datasets</button>
        <button class="btn btn-sm btn-ghost" data-act="download-zip">
          <svg class="ico ico-sm"><use href="#i-download"/></svg> Download ZIP</button>
        ${canSubmitReview
          ? `<button class="btn btn-sm btn-ghost" data-act="submit-review">
               <svg class="ico ico-sm"><use href="#i-graduation"/></svg> ${esc(submitLabel)}</button>`
          : isOwner && rs === 'in_review'
            ? `<button class="btn btn-sm btn-ghost" disabled aria-disabled="true"
                 title="This project is with a reviewer.">
                 <svg class="ico ico-sm"><use href="#i-clock"/></svg> In review</button>`
            : isOwner && rs === 'approved'
              ? `<span class="pill good" style="align-self:center">
                   <svg class="ico ico-sm"><use href="#i-globe"/></svg> Public</span>`
              : ''}
      </div>
      <div class="ws-prj-msg"></div>
      <div class="ws-link-slot" style="margin:0"></div>

      <div class="prj-d-divider"></div>

      <div class="prj-d-cols">
        <div class="prj-d-col prj-d-vis">
          <div class="prj-panel-hdr">
            <svg class="ico"><use href="#i-shield"/></svg>
            <h3>Visibility</h3>
          </div>
          <p class="prj-panel-lede muted">
            Visibility is the single setting that decides who can find and open
            this project. ${isOwner ? 'Change it any time.' : 'Only the project owner can change it.'}</p>
          <div class="ws-vis-slot"></div>
        </div>
        <div class="prj-d-col prj-d-files">
          <div class="ws-files-slot"></div>
        </div>
      </div>
    </div>`;

  const msg = ctx.el.querySelector<HTMLElement>('.ws-prj-msg')!;
  const showErr = (text: string) => {
    msg.innerHTML = `<div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">${esc(text)}</div>`;
  };

  // Copy the project ID (slug handle) to the clipboard
  ctx.el.querySelector<HTMLButtonElement>('button[data-act="copy-id"]')
    ?.addEventListener('click', async (e) => {
      const btn = e.currentTarget as HTMLButtonElement;
      try {
        await navigator.clipboard.writeText(p.slug);
        btn.innerHTML = '<svg class="ico ico-sm"><use href="#i-check"/></svg>';
        toast('Project ID copied');
        window.setTimeout(() => { btn.innerHTML = '<svg class="ico ico-sm"><use href="#i-copy"/></svg>'; }, 1400);
      } catch { toast('Copy failed; select and copy the ID manually'); }
    });

  // Download the project as a role-protected ZIP
  ctx.el.querySelector<HTMLButtonElement>('button[data-act="download-zip"]')
    ?.addEventListener('click', async (e) => {
      const btn = e.currentTarget as HTMLButtonElement;
      btn.disabled = true;
      msg.innerHTML = '';
      try {
        const r = await authFetch(`/gw/projects/${encodeURIComponent(p.id)}/download.zip`);
        if (!r.ok) { showErr(`Could not download: ${await errText(r)}`); return; }
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = `${p.slug || p.name || 'project'}.zip`;
        document.body.appendChild(a); a.click(); a.remove();
        URL.revokeObjectURL(url);
      } catch (err: any) {
        showErr(`Could not download: ${err?.message || String(err)}`);
      } finally { btn.disabled = false; }
    });

  // Link datasets (inline panel; POST /gw/datasets/{id}/projects)
  const linkSlot = ctx.el.querySelector<HTMLElement>('.ws-link-slot')!;
  const drawLinkPanel = async () => {
    linkSlot.innerHTML = `<div class="prj-panel" style="margin:.6rem 0 0"><p class="muted" style="margin:0">Loading datasets…</p></div>`;
    let mine: any[] = [], linkedIds = new Set<string>();
    try {
      const [rMine, rLinked] = await Promise.all([
        authFetch('/gw/datasets?owner=me'),
        authFetch(`/gw/datasets?project_id=${encodeURIComponent(p.id)}`),
      ]);
      if (rMine.ok) { const j = await rMine.json(); mine = (Array.isArray(j) ? j : (j.datasets || j.items || [])); }
      if (rLinked.ok) { const j = await rLinked.json(); (Array.isArray(j) ? j : (j.datasets || j.items || [])).forEach((d: any) => linkedIds.add(d.id)); }
    } catch { /* empty */ }
    const rows = mine.length ? mine.map((d: any) => {
      const linked = linkedIds.has(d.id);
      return `<div style="display:flex;align-items:center;gap:.6rem;padding:.4rem .2rem;border-bottom:1px solid var(--border)">
        <svg class="ico ico-sm" style="opacity:.6"><use href="#i-database"/></svg>
        <span style="flex:1;font-size:.85rem;color:${theme.ink[0]}">${esc(d.title || d.name || d.id)}</span>
        ${linked
          ? `<span class="pill good">linked</span>`
          : `<button class="btn btn-sm btn-primary" data-act="do-link" data-did="${esc(d.id)}">Link</button>`}
      </div>`;
    }).join('') : `<p class="muted" style="margin:.4rem 0">You have no datasets yet. Generate one on the Datasets page.</p>`;
    linkSlot.innerHTML = `
      <div class="prj-panel" style="margin:.6rem 0 0">
        <div class="prj-panel-hdr"><svg class="ico"><use href="#i-database"/></svg><h3>Link datasets to this project</h3>
          <span style="flex:1"></span>
          <button class="btn btn-sm btn-ghost" data-act="link-close">Close</button></div>
        <p class="prj-panel-lede muted">Linked datasets are copied into the project's files and appear in JupyterLab.</p>
        ${rows}
      </div>`;
    linkSlot.querySelector<HTMLButtonElement>('button[data-act="link-close"]')?.addEventListener('click', () => { linkSlot.innerHTML = ''; });
    linkSlot.querySelectorAll<HTMLButtonElement>('button[data-act="do-link"]').forEach(btn => {
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
          const r = await authFetch(`/gw/datasets/${encodeURIComponent(btn.dataset.did!)}/projects`, {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ project_id: p.id }),
          });
          if (!r.ok) { showErr(`Could not link: ${await errText(r)}`); btn.disabled = false; return; }
          toast('Dataset linked'); await drawLinkPanel();
        } catch (e: any) { showErr(`Could not link: ${e?.message || e}`); btn.disabled = false; }
      });
    });
  };
  ctx.el.querySelector<HTMLButtonElement>('button[data-act="link-datasets"]')
    ?.addEventListener('click', () => { if (linkSlot.innerHTML.trim()) linkSlot.innerHTML = ''; else void drawLinkPanel(); });

  // Clone a public project into a private copy I own (then open THAT)
  ctx.el.querySelector<HTMLButtonElement>('button[data-act="clone"]')
    ?.addEventListener('click', async (e) => {
      const btn = e.currentTarget as HTMLButtonElement;
      btn.disabled = true; msg.innerHTML = '';
      try {
        const r = await authFetch(`/gw/projects/${encodeURIComponent(p.id)}/clone`, {
          method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}',
        });
        if (!r.ok) { showErr(`Could not clone: ${await errText(r)}`); btn.disabled = false; return; }
        const nw = await r.json().catch(() => ({} as any));
        toast(`Cloned to "${nw.name || 'a new project'}" (${nw.files_copied ?? 0} file(s) copied)`);
        if (nw.id) { ctx.filters.ws_project = nw.id; ctx.bus.set('ws_project', nw.id); await draw(ctx); }
      } catch (err: any) {
        showErr(`Could not clone: ${err?.message || String(err)}`); btn.disabled = false;
      }
    });

  // Open in JupyterLab
  ctx.el.querySelector<HTMLButtonElement>('button[data-act="open-lab"]')
    ?.addEventListener('click', async (e) => {
      const btn = e.currentTarget as HTMLButtonElement;
      btn.disabled = true;
      msg.innerHTML = '';
      try {
        const r = await authFetch('/gw/workspace/spawn', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ project_id: p.id }),
        });
        if (!r.ok) { showErr(`Could not open the lab: ${await errText(r)}`); btn.disabled = false; return; }
        const body = await r.json().catch(() => ({} as any));
        const url = body?.url || hubRunningUrl;
        if (url) window.open(url, '_blank', 'noreferrer');
        else toast('Lab spawn requested. The pod is starting; reopen once it is up');
        btn.disabled = false;
      } catch (err: any) {
        showErr(`Could not open the lab: ${err?.message || String(err)}`);
        btn.disabled = false;
      }
    });

  // Submit for review (owner-only): opens the project-submit modal, which
  //    runs the essential-files check and POSTs the submit. On success it
  // re-draws this detail (in_review) via onDone.
  ctx.el.querySelector<HTMLButtonElement>('button[data-act="submit-review"]')
    ?.addEventListener('click', () => {
      ctx.host.openModal('project-submit', {
        project_id: p.id,
        project_name: p.name,
        onDone: () => { void draw(ctx); },
      });
    });

  // visibility control (owner-only PATCH; group picker lazily)
  const visSlot = ctx.el.querySelector<HTMLElement>('.ws-vis-slot')!;
  const vs = {
    pendingVis: p.visibility as ProjectVisibility,
    pendingGroup: p.group_id as string | null,
    groups: null as GroupRow[] | null,
    groupsErr: null as string | null,
    busy: false,
    saveErr: null as string | null,
  };

  const groupName = (id: string | null): string => {
    if (!id) return '';
    return p.group_name || vs.groups?.find(g => g.id === id)?.name || id;
  };

  const paintVis = () => {
    const changed = vs.pendingVis !== p.visibility
      || (vs.pendingVis === 'group' && vs.pendingGroup !== p.group_id);
    const needsGroup = vs.pendingVis === 'group' && !vs.pendingGroup;

    let groupHtml = '';
    if (vs.pendingVis === 'group') {
      if (!isOwner) {
        groupHtml = `<p class="muted" style="font-size:.82rem;margin:0">
          Shared with <strong>${esc(groupName(p.group_id) || 'a group')}</strong>.</p>`;
      } else if (vs.groupsErr) {
        groupHtml = `<div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">
          Could not load your groups: ${esc(vs.groupsErr)}</div>`;
      } else if (vs.groups === null) {
        groupHtml = `<p class="muted">Loading your groups…</p>`;
      } else if (vs.groups.length === 0) {
        groupHtml = `<p class="muted" style="font-size:.82rem;margin:0">
          You're not a member of any group yet. Join one from the Community
          section to share a project with it.</p>`;
      } else {
        groupHtml = `
          <label class="prj-vis-group-label">
            <span>Share with group</span>
            <select class="ws-vis-group">
              <option value="">Choose a group</option>
              ${vs.groups.map(g =>
                `<option value="${esc(g.id)}"${vs.pendingGroup === g.id ? ' selected' : ''}>${esc(g.name)}</option>`).join('')}
            </select>
          </label>`;
      }
    }

    visSlot.innerHTML = `
      <div class="prj-vis-options">
        ${VISIBILITIES.map(v => `
          <button type="button" class="prj-vis-opt${vs.pendingVis === v ? ' active' : ''}"
                  data-vis="${esc(v)}" ${isOwner ? '' : 'disabled'} aria-pressed="${vs.pendingVis === v}">
            <svg class="ico"><use href="#${esc(visibilityIcon(v))}"/></svg>
            <span class="prj-vis-opt-main">
              <span class="prj-vis-opt-name">${esc(visibilityLabel(v))}</span>
              <span class="prj-vis-opt-hint">${esc(visibilityHint(v))}</span>
            </span>
            <span class="prj-vis-radio" aria-hidden="true"></span>
          </button>`).join('')}
      </div>
      ${groupHtml ? `<div class="prj-vis-group">${groupHtml}</div>` : ''}
      ${vs.saveErr ? `
        <div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">
          Could not update visibility: ${esc(vs.saveErr)}</div>` : ''}
      ${isOwner ? `
        <div class="prj-vis-foot">
          <button class="btn btn-sm btn-primary" data-act="save"
                  ${vs.busy || !changed || needsGroup ? 'disabled' : ''}>
            <svg class="ico ico-sm"><use href="#i-check"/></svg>
            ${vs.busy ? 'Saving…' : 'Save visibility'}</button>
          ${changed && !vs.busy
            ? '<button class="btn btn-sm btn-ghost" data-act="cancel">Cancel</button>' : ''}
        </div>` : ''}`;

    visSlot.querySelectorAll<HTMLButtonElement>('button[data-vis]').forEach(btn => {
      btn.addEventListener('click', () => {
        if (!isOwner) return;
        vs.pendingVis = btn.dataset.vis as ProjectVisibility;
        // The group picker only lists groups you belong to: fetched lazily.
        if (vs.pendingVis === 'group' && vs.groups === null && !vs.groupsErr) {
          listGroups()
            .then(g => { vs.groups = g.filter(x => x.i_am_a_member); paintVis(); })
            .catch(e => { vs.groupsErr = e?.message || String(e); paintVis(); });
        }
        paintVis();
      });
    });
    visSlot.querySelector<HTMLSelectElement>('select.ws-vis-group')
      ?.addEventListener('change', (e) => {
        vs.pendingGroup = (e.currentTarget as HTMLSelectElement).value || null;
        paintVis();
      });
    visSlot.querySelector<HTMLButtonElement>('button[data-act="cancel"]')
      ?.addEventListener('click', () => {
        vs.pendingVis = p.visibility as ProjectVisibility;
        vs.pendingGroup = p.group_id;
        vs.saveErr = null;
        paintVis();
      });
    visSlot.querySelector<HTMLButtonElement>('button[data-act="save"]')
      ?.addEventListener('click', async () => {
        const changedNow = vs.pendingVis !== p.visibility
          || (vs.pendingVis === 'group' && vs.pendingGroup !== p.group_id);
        if (!changedNow || (vs.pendingVis === 'group' && !vs.pendingGroup)) return;
        vs.busy = true; vs.saveErr = null;
        paintVis();
        try {
          const patch = vs.pendingVis === 'group'
            ? { visibility: vs.pendingVis, group_id: vs.pendingGroup }
            : { visibility: vs.pendingVis };
          await updateProject(p.id, patch);
          toast('Project visibility updated');
          await draw(ctx);   // re-read the authoritative row
        } catch (e: any) {
          vs.saveErr = e?.message || String(e);
          vs.busy = false;
          paintVis();
        }
      });
  };

  paintVis();

  // Project files tree: compose ws-project-files INSIDE this detail column.
  //    It reads ctx.filters.ws_project (same as us) and owns its own heading +
  // "New" menu + send/upload actions; we just hand it a slot + scoped empty.
  const filesSlot = ctx.el.querySelector<HTMLElement>('.ws-files-slot');
  if (filesSlot) {
    const filesCtx: WidgetCtx = {
      ...ctx, el: filesSlot, empty: (m?: string) => emptyState(filesSlot, m),
    };
    try { await wsProjectFiles.render(filesCtx); }
    catch { emptyState(filesSlot, 'Could not load the project files.'); }
  }

  void theme;
}

const def: WidgetDef = {
  id: 'ws-project-detail',
  reactsTo: ['ws_project'],
  render(ctx) { return draw(ctx); },
};
export default def;
