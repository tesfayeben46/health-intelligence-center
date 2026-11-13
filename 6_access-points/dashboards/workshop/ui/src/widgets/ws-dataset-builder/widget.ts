/** ws-dataset-builder: governed dataset generation.
 *
 *   GET  /gw/datasets/templates: {items:[{id,title,description,mart}]}
 *   POST /gw/datasets/generate  {title, description?, project_id (OPTIONAL),
 *                                tier: personal|shared|public,
 *                                template_id? | sql?}
 *: 201 {artifact_id, rows, byte_count, …}
 *
 * The generate route executes through the one governed ad-hoc SQL chain
 * (row-scope guard, per-table OPA, Trino, audit): a 403 here is a real OPA
 * denial and its reason is shown verbatim; a 400 surfaces the gateway
 * message. Datasets are independent entities, so a project is optional: pick
 * one to link the result into it (M:N), or leave it
 * unlinked. On success: entity cache invalidated, toast, and a `ws_datasets`
 * bus poke so the catalog table refreshes. */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import { invalidateEntityData } from '../../runtime/dataHook';
import { esc, errText, toast, unwrapList } from '../_shared/helpers';

type Template = { id: string; title: string; description: string; mart: string };
type ProjectRow = { id: string; name: string; status?: string };

async function draw(ctx: WidgetCtx) {
  const { theme } = ctx;
  let templates: Template[] = [];
  let tplErr = '';
  let projects: ProjectRow[] = [];
  let projErr = '';
  try {
    const [rT, rP] = await Promise.all([
      authFetch('/gw/datasets/templates'),
      authFetch('/gw/projects'),
    ]);
    if (rT.ok) templates = unwrapList<Template>(await rT.json());
    else tplErr = await errText(rT);
    if (rP.ok) projects = unwrapList<ProjectRow>(await rP.json(), 'projects');
    else projErr = await errText(rP);
  } catch (e: any) { tplErr = tplErr || e?.message || String(e); }

  if (!templates.length && tplErr) {
    ctx.empty(`Dataset templates unavailable: ${tplErr}`);
    return;
  }

  const activeProjects = projects.filter(p => !p.status || p.status === 'active');

  ctx.el.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:.8rem">
      <p style="font-size:.84rem;color:${theme.ink[2]};margin:0;max-width:78ch">
        Pick a curated starter over a governed mart (or write your own SQL)
        and the builder runs it through the governed query chain and catalogs
        the result as a real, independent dataset. Optionally link it to a
        project.</p>

      <div class="ws-tpl-grid">
        ${templates.map(t => `
          <button type="button" class="ws-tpl-card" data-tpl="${esc(t.id)}">
            <div class="ws-tpl-title">
              <svg class="ico ico-sm"><use href="#i-database"/></svg>
              <strong>${esc(t.title)}</strong>
            </div>
            <p>${esc(t.description)}</p>
            <code>${esc(t.mart)}</code>
          </button>`).join('')}
        <button type="button" class="ws-tpl-card" data-tpl="__sql__">
          <div class="ws-tpl-title">
            <svg class="ico ico-sm"><use href="#i-tools"/></svg>
            <strong>From SQL</strong>
          </div>
          <p>Write a governed query over the marts your role can read; same
          row-scope and OPA enforcement as every other query.</p>
          <code>SELECT … FROM bi_*.mart_*</code>
        </button>
      </div>

      <form class="ws-gen wk-form" style="display:none;border:1px solid var(--line);border-radius:10px;padding:.9rem 1rem;gap:.55rem">
        <div class="ws-gen-src" style="font-size:.8rem;color:${theme.ink[2]}"></div>
        <textarea name="sql" rows="4" style="display:none;font-family:${theme.fontMono};font-size:.8rem"
          placeholder="SELECT region, disease, SUM(cases) FROM bi_idsr.mart_idsr_disease_weekly GROUP BY 1,2"></textarea>
        <label><span>Title</span>
          <input name="title" minlength="3" maxlength="120" placeholder="e.g. Weekly malaria counts, 2025" required></label>
        <label><span>Description <span class="muted">· optional</span></span>
          <input name="description" placeholder="What is in this dataset and why?"></label>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:.6rem">
          <label><span>Link to project <span class="muted">· optional</span></span>
            <select name="project">
              <option value="">None (independent)</option>
              ${activeProjects.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}
            </select></label>
          <label><span>Tier</span>
            <select name="tier">
              <option value="personal">Personal (only me)</option>
              <option value="shared">Shared (my group)</option>
              <option value="public">Public (everyone)</option>
            </select></label>
        </div>
        ${projErr ? `
          <p class="muted" style="font-size:.8rem;margin:0">Could not load your projects: ${esc(projErr)}. You can still generate an unlinked dataset.</p>` : ''}
        <div class="ws-gen-out"></div>
        <div style="display:flex;gap:.5rem;justify-content:flex-end">
          <button type="button" class="btn btn-sm btn-ghost" data-act="cancel">Cancel</button>
          <button type="submit" class="btn btn-sm btn-primary">
            <svg class="ico ico-sm"><use href="#i-play"/></svg> Generate dataset</button>
        </div>
      </form>
    </div>`;

  const form = ctx.el.querySelector<HTMLFormElement>('form.ws-gen')!;
  const srcNote = form.querySelector<HTMLElement>('.ws-gen-src')!;
  const sqlBox = form.querySelector<HTMLTextAreaElement>('textarea[name="sql"]')!;
  const out = form.querySelector<HTMLElement>('.ws-gen-out')!;
  let selectedTpl: string | null = null;

  const selectCard = (tpl: string) => {
    selectedTpl = tpl;
    ctx.el.querySelectorAll('.ws-tpl-card').forEach(c =>
      c.classList.toggle('active', (c as HTMLElement).dataset.tpl === tpl));
    form.style.display = 'flex';
    form.style.flexDirection = 'column';
    out.innerHTML = '';
    if (tpl === '__sql__') {
      sqlBox.style.display = '';
      srcNote.innerHTML = 'Source: <strong>your own governed SQL</strong> (executed with your role’s scopes).';
    } else {
      sqlBox.style.display = 'none';
      const t = templates.find(x => x.id === tpl);
      srcNote.innerHTML = `Source: template <strong>${esc(t?.title || tpl)}</strong> over <code>${esc(t?.mart || '')}</code>.`;
    }
    form.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  };

  ctx.el.querySelectorAll<HTMLButtonElement>('.ws-tpl-card').forEach(card =>
    card.addEventListener('click', () => selectCard(card.dataset.tpl!)));

  form.querySelector<HTMLButtonElement>('button[data-act="cancel"]')!
    .addEventListener('click', () => {
      form.style.display = 'none';
      selectedTpl = null;
      ctx.el.querySelectorAll('.ws-tpl-card').forEach(c => c.classList.remove('active'));
    });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!selectedTpl) return;
    const title = (form.elements.namedItem('title') as HTMLInputElement).value.trim();
    const description = (form.elements.namedItem('description') as HTMLInputElement).value.trim();
    const project_id = (form.elements.namedItem('project') as HTMLSelectElement).value;
    const tier = (form.elements.namedItem('tier') as HTMLSelectElement).value;
    const sql = sqlBox.value.trim();
    if (!title) return;   // project is optional (datasets are independent)
    if (selectedTpl === '__sql__' && !sql) {
      out.innerHTML = `<div class="alert-banner" style="background:var(--watch-50);color:var(--watch)">Write the SQL to run, or pick a template instead.</div>`;
      return;
    }
    const submit = form.querySelector<HTMLButtonElement>('button[type="submit"]')!;
    submit.disabled = true;
    out.innerHTML = `<p class="muted" style="font-size:.82rem;margin:0">Running the governed query and cataloging the result…</p>`;
    try {
      const body: Record<string, unknown> = { title, project_id, tier };
      if (description) body.description = description;
      if (selectedTpl === '__sql__') body.sql = sql;
      else body.template_id = selectedTpl;
      const r = await authFetch('/gw/datasets/generate', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (r.status === 201) {
        const res = await r.json();
        invalidateEntityData();
        toast(`Dataset generated · ${Number(res.rows ?? 0).toLocaleString()} rows`);
        ctx.bus.set('ws_datasets', String(Date.now()));   // refresh the catalog table
        out.innerHTML = `
          <div class="alert-banner" style="background:var(--good-50);color:var(--good)">
            Generated <strong>&nbsp;${esc(title)}&nbsp;</strong>:
            ${esc(Number(res.rows ?? 0).toLocaleString())} rows ·
            ${esc(Number(res.byte_count ?? 0).toLocaleString())} bytes ·
            artifact <code>${esc(res.artifact_id || '?')}</code>.
            Find it below in Generated datasets.
          </div>`;
        return;
      }
      const detail = await errText(r);
      if (r.status === 403) {
        out.innerHTML = `<div class="alert-banner" style="background:var(--watch-50);color:var(--watch)">
          Denied by policy: ${esc(detail)}</div>`;
      } else {
        out.innerHTML = `<div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">
          Generation failed (HTTP ${r.status}): ${esc(detail)}</div>`;
      }
    } catch (err: any) {
      out.innerHTML = `<div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">
        Generation failed: ${esc(err?.message || String(err))}</div>`;
    } finally {
      submit.disabled = false;
    }
  });
}

const def: WidgetDef = {
  id: 'ws-dataset-builder',
  reactsTo: [],
  render(ctx) { return draw(ctx); },
};
export default def;
