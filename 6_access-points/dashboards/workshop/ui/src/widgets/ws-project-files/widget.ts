/** ws-project-files: the project's file space as a nested, expandable tree
 *  on the master-detail Projects page; it lives inside the right
 *  project-detail column, next to the Visibility control.
 *  reactsTo `ws_project`.
 *
 *   GET  /gw/projects/{id}/files: {files:[{name,size,…}], folders:[]}
 *   POST /gw/projects/{id}/files/folder     {path}          create nested folder
 *   POST /gw/projects/{id}/files/upload     multipart(path,file)  upload
 *   POST /gw/projects/{id}/send             {target_project_id,item_type:'file',
 *                                            item_ref}       copy into another
 *                                            project's received/ inbox
 * No storage credentials reach the browser: the gateway proxies the object store. */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import { listProjects } from '../../api/projects';
import { fmtBytes, fmtDateTime } from '../../api/projects';
import { esc, errText, toast } from '../_shared/helpers';

type FileRow = { name: string; size: number; last_modified: string | null };

type TreeNode = {
  name: string; path: string; kind: 'folder' | 'file';
  size?: number; mtime?: string | null;
  children: Map<string, TreeNode>;
};

/** Extension to {icon symbol, colour class} for the file leaf glyph. */
function fileGlyph(name: string): { icon: string; cls: string } {
  const ext = (name.split('.').pop() || '').toLowerCase();
  if (['csv', 'tsv', 'xlsx', 'parquet'].includes(ext)) return { icon: 'i-grid', cls: 'data' };
  if (['ipynb'].includes(ext)) return { icon: 'i-notebook', cls: 'nb' };
  if (['pkl', 'joblib', 'onnx', 'h5', 'pt', 'bin'].includes(ext)) return { icon: 'i-cpu', cls: 'model' };
  if (['pdf'].includes(ext)) return { icon: 'i-file', cls: 'doc' };
  if (['md', 'txt', 'json', 'yaml', 'yml'].includes(ext)) return { icon: 'i-file', cls: 'text' };
  return { icon: 'i-file', cls: 'text' };
}

function makeFolder(name: string, path: string): TreeNode {
  return { name, path, kind: 'folder', children: new Map() };
}

async function draw(ctx: WidgetCtx) {
  const projectId = ctx.filters.ws_project || '';
  if (!projectId) { ctx.empty('Select a project to browse its file space.'); return; }

  // Collapsed folder paths (default: everything expanded, like the reference).
  const collapsed = new Set<string>();
  let projects: { id: string; name: string }[] = [];
  try { projects = (await listProjects({ scope: 'all' })).map(p => ({ id: p.id, name: p.name })); } catch { /* send picker degrades */ }

  const load = async () => {
    let files: FileRow[] = [], folders: string[] = [];
    try {
      const r = await authFetch(`/gw/projects/${encodeURIComponent(projectId)}/files`);
      if (!r.ok) { ctx.el.innerHTML = shell(`<div class="widget-empty">Could not list files: ${esc(await errText(r))}</div>`); wireTools(); return; }
      const j = await r.json();
      files = j.files || []; folders = j.folders || [];
    } catch (e: any) { ctx.el.innerHTML = shell(`<div class="widget-empty">Could not list files: ${esc(e?.message || String(e))}</div>`); wireTools(); return; }

    // Build a nested tree from the flat folder + file path lists.
    const root = makeFolder('', '');
    const ensureFolder = (path: string): TreeNode => {
      if (!path) return root;
      const parts = path.split('/').filter(Boolean);
      let cur = root, acc = '';
      for (const part of parts) {
        acc = acc ? `${acc}/${part}` : part;
        if (!cur.children.has(part)) cur.children.set(part, makeFolder(part, acc));
        cur = cur.children.get(part)!;
      }
      return cur;
    };
    folders.forEach(f => ensureFolder(f));
    files.forEach(f => {
      const parts = f.name.split('/').filter(Boolean);
      const leaf = parts.pop() || f.name;
      const parent = ensureFolder(parts.join('/'));
      parent.children.set(leaf, {
        name: leaf, path: f.name, kind: 'file',
        size: f.size, mtime: f.last_modified, children: new Map(),
      });
    });

    const renderNode = (node: TreeNode, depth: number): string => {
      const kids = [...node.children.values()].sort((a, b) => {
        if (a.kind !== b.kind) return a.kind === 'folder' ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
      return kids.map(n => {
        const pad = `style="padding-left:${0.35 + depth * 1.05}rem"`;
        if (n.kind === 'folder') {
          const isOpen = !collapsed.has(n.path);
          return `
            <div class="prj-tree-row prj-tree-folder" data-folder="${esc(n.path)}" ${pad}>
              <svg class="ico prj-tree-caret${isOpen ? ' open' : ''}"><use href="#i-chevron-right"/></svg>
              <svg class="ico prj-tree-ico folder"><use href="#i-folder"/></svg>
              <span class="prj-tree-name">${esc(n.name)}/</span>
            </div>
            ${isOpen ? renderNode(n, depth + 1) : ''}`;
        }
        const g = fileGlyph(n.name);
        return `
          <div class="prj-tree-row prj-tree-file" ${pad}
               title="${esc(n.name)} · ${esc(fmtBytes(n.size))} · ${esc(fmtDateTime(n.mtime))}">
            <span class="prj-tree-caret-spacer"></span>
            <svg class="ico prj-tree-ico ${g.cls}"><use href="#${g.icon}"/></svg>
            <span class="prj-tree-name">${esc(n.name)}</span>
            <span class="prj-tree-size">${esc(fmtBytes(n.size))}</span>
            <button class="prj-tree-send btn btn-sm btn-ghost" data-send="${esc(n.path)}" title="Send to another project">
              <svg class="ico ico-sm"><use href="#i-arrow-right"/></svg></button>
          </div>`;
      }).join('');
    };

    const hasAny = root.children.size > 0;
    const body = hasAny
      ? `<div class="prj-tree">${renderNode(root, 0)}</div>`
      : `<div class="prj-files-empty">
           <svg class="ico"><use href="#i-folder"/></svg>
           <p>This project is empty. Use <strong>New</strong> to add a folder or
           upload a file. JupyterLab writes here too, and linked datasets appear
           under <code>datasets/</code>.</p>
         </div>`;
    ctx.el.innerHTML = shell(body);
    wireTools();

    // Folder expand / collapse.
    ctx.el.querySelectorAll<HTMLElement>('.prj-tree-folder').forEach(r =>
      r.addEventListener('click', () => {
        const p = r.dataset.folder!;
        if (collapsed.has(p)) collapsed.delete(p); else collapsed.add(p);
        void load();
      }));

    // Per-file "send to another project".
    const msg = ctx.el.querySelector<HTMLElement>('.prj-files-msg')!;
    const showMsg = (h: string) => { msg.style.display = ''; msg.innerHTML = h; };
    ctx.el.querySelectorAll<HTMLButtonElement>('button[data-send]').forEach(btn =>
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const filePath = btn.dataset.send!;
        const targets = projects.filter(p => p.id !== projectId);
        if (!targets.length) { showMsg(`<div class="prj-send-box">No other project to send to.</div>`); return; }
        showMsg(`
          <div class="prj-send-box">
            <span>Send <code>${esc(filePath.split('/').pop() || filePath)}</code> to</span>
            <select class="prj-send-target">${targets.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}</select>
            <button class="btn btn-sm btn-primary" data-act="do-send">Send</button>
            <button class="btn btn-sm btn-ghost" data-act="cancel-send">Cancel</button>
          </div>`);
        msg.querySelector('[data-act="cancel-send"]')!.addEventListener('click', () => { msg.style.display = 'none'; });
        msg.querySelector('[data-act="do-send"]')!.addEventListener('click', async () => {
          const target = (msg.querySelector('.prj-send-target') as HTMLSelectElement).value;
          const r = await authFetch(`/gw/projects/${encodeURIComponent(projectId)}/send`,
            { method: 'POST', headers: { 'content-type': 'application/json' },
              body: JSON.stringify({ target_project_id: target, item_type: 'file', item_ref: filePath }) });
          if (r.ok) { const j = await r.json(); toast(`Sent to ${j.target_name} · received/`); msg.style.display = 'none'; }
          else showMsg(`<div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">Send failed: ${esc(await errText(r))}</div>`);
        });
      }));
  };

  /** Panel chrome: the "Project files" heading + the "New" menu, then `inner`. */
  const shell = (inner: string): string => `
    <div class="prj-files2">
      <div class="prj-panel-hdr">
        <svg class="ico"><use href="#i-file"/></svg>
        <h3>Project files</h3>
        <span style="flex:1"></span>
        <div class="prj-new">
          <button class="btn btn-sm" data-act="new-menu" aria-haspopup="true" aria-expanded="false">
            <svg class="ico ico-sm"><use href="#i-plus"/></svg> New
            <svg class="ico ico-sm"><use href="#i-chevron-down"/></svg>
          </button>
          <div class="prj-new-menu" hidden>
            <button type="button" data-act="mkdir"><svg class="ico ico-sm"><use href="#i-folder"/></svg> New folder</button>
            <label data-act="upload-label"><svg class="ico ico-sm"><use href="#i-upload"/></svg> Upload file
              <input type="file" data-act="upload" hidden></label>
          </div>
        </div>
      </div>
      ${inner}
      <div class="prj-files-msg" style="display:none"></div>
    </div>`;

  /** Wire the "New" menu (folder / upload). Both target the project root: deep
   *  operations happen in JupyterLab, matching the reference's panel-level New. */
  const wireTools = () => {
    const menuBtn = ctx.el.querySelector<HTMLButtonElement>('button[data-act="new-menu"]');
    const menu = ctx.el.querySelector<HTMLElement>('.prj-new-menu');
    const msg = ctx.el.querySelector<HTMLElement>('.prj-files-msg')!;
    const showMsg = (h: string) => { msg.style.display = ''; msg.innerHTML = h; };
    if (menuBtn && menu) {
      const closeMenu = () => { menu.hidden = true; menuBtn.setAttribute('aria-expanded', 'false'); };
      menuBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        menu.hidden = !menu.hidden;
        menuBtn.setAttribute('aria-expanded', String(!menu.hidden));
      });
      document.addEventListener('click', closeMenu, { once: true, capture: false });
      menu.addEventListener('click', (e) => e.stopPropagation());
    }

    ctx.el.querySelector<HTMLButtonElement>('button[data-act="mkdir"]')
      ?.addEventListener('click', async () => {
        if (menu) menu.hidden = true;
        const name = window.prompt('New folder name'); if (!name) return;
        const r = await authFetch(`/gw/projects/${encodeURIComponent(projectId)}/files/folder`,
          { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ path: name }) });
        if (r.ok) { toast(`Folder created: ${name}`); void load(); }
        else showMsg(`<div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">Could not create folder: ${esc(await errText(r))}</div>`);
      });

    ctx.el.querySelector<HTMLInputElement>('input[data-act="upload"]')
      ?.addEventListener('change', async (e) => {
        if (menu) menu.hidden = true;
        const f = (e.currentTarget as HTMLInputElement).files?.[0]; if (!f) return;
        const fd = new FormData(); fd.set('path', ''); fd.set('file', f);
        const r = await authFetch(`/gw/projects/${encodeURIComponent(projectId)}/files/upload`, { method: 'POST', body: fd });
        if (r.ok) { toast(`Uploaded ${f.name}`); void load(); }
        else showMsg(`<div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">Upload failed: ${esc(await errText(r))}</div>`);
      });
  };

  await load();
}

const def: WidgetDef = {
  id: 'ws-project-files',
  reactsTo: ['ws_project'],
  render(ctx) { return draw(ctx); },
};
export default def;
