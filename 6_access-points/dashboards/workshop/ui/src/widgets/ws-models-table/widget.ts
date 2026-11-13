/** ws-models-table: the model catalogue (registry ∪ artifact bindings).
 *
 *   GET /gw/ml                      MLflow registry joined to ops_ml
 *                                   deployments: name, aliases, kind, updated
 *   GET /gw/artifacts?kind=model    tier + project binding via
 *                                   artifact.mlflow_model_name (artifacts may
 *                                   not exist for every model, shown as a dash)
 *
 * Filters: tier (Private/Group/Public and the artifact tiers personal/shared/
 * public) + project + name search. The catalog can pin the tier surface per
 * role (widget filter `artifact_tier`). Clicking a row publishes the
 * `ws_model` dimension on the filter bus; the workbench and monitoring
 * widgets below react to it. Lifecycle vocabulary is @champion/@challenger
 * aliases, never MLflow stage names. */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import { listModels, fmtEpochMs, aliasPillClass, type MlModelSummary } from '../../api/ml';
import { esc, errText, unwrapList, tierLabel } from '../_shared/helpers';

type ModelArtifact = {
  id: string; title: string; tier: string; project_id: string | null;
  mlflow_model_name: string | null;
};
type ProjectRow = { id: string; name: string };

const TIER_OPTIONS: { key: string; label: string }[] = [
  { key: 'personal', label: 'Private' },
  { key: 'shared', label: 'Group' },
  { key: 'public', label: 'Public' },
];

async function draw(ctx: WidgetCtx) {
  const { theme } = ctx;

  const tierFilter = (ctx.widget.local_filters || []).find(f => f.id === 'artifact_tier');
  const allowedTiers: string[] | null = tierFilter?.allowed_values ?? null;
  const tierOptions = TIER_OPTIONS.filter(t => !allowedTiers || allowedTiers.includes(t.key));

  let models: MlModelSummary[] = [];
  let mlErr = '';
  let artifacts: ModelArtifact[] = [];
  let projects: ProjectRow[] = [];
  try {
    models = await listModels();
  } catch (e: any) { mlErr = e?.message || String(e); }
  try {
    const rA = await authFetch('/gw/artifacts?kind=model&limit=200');
    if (rA.ok) artifacts = unwrapList<ModelArtifact>(await rA.json());
  } catch { /* bindings degrade to a dash */ }
  try {
    const rP = await authFetch('/gw/projects');
    if (rP.ok) projects = unwrapList<ProjectRow>(await rP.json(), 'projects');
  } catch { /* project filter degrades */ }

  if (!models.length) {
    ctx.empty(mlErr
      ? `Could not load the model registry: ${mlErr}`
      : 'The registry is empty. Models appear once a package is trained and registered by the ML runner.');
    return;
  }

  const projectName = new Map(projects.map(p => [p.id, p.name]));
  const bindingFor = (name: string): ModelArtifact | undefined =>
    artifacts.find(a => a.mlflow_model_name === name) ||
    artifacts.find(a => a.title === name);

  const state = { tier: '', project: '', q: '' };

  ctx.el.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:.7rem">
      <div style="display:flex;gap:.6rem;flex-wrap:wrap;align-items:center">
        <select class="art-sort ws-flt-tier" aria-label="Tier">
          <option value="">All tiers</option>
          ${tierOptions.map(t => `<option value="${esc(t.key)}">${esc(t.label)}</option>`).join('')}
        </select>
        <select class="art-sort ws-flt-project" aria-label="Project">
          <option value="">All projects</option>
          ${projects.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}
        </select>
        <span class="art-search" style="flex:1;min-width:200px;margin:0">
          <svg class="ico"><use href="#i-search"/></svg>
          <input class="ws-flt-q" placeholder="Search models by name" aria-label="Search models">
        </span>
      </div>
      <div class="ws-tbl-out"></div>
    </div>`;

  const out = ctx.el.querySelector<HTMLElement>('.ws-tbl-out')!;

  const paint = () => {
    const term = state.q.toLowerCase();
    const rows = models.filter(m => {
      const b = bindingFor(m.name);
      if (state.tier && (b?.tier ?? '') !== state.tier) return false;
      if (state.project && (b?.project_id ?? '') !== state.project) return false;
      if (allowedTiers && b && !allowedTiers.includes(b.tier)) return false;
      if (term && !m.name.toLowerCase().includes(term)) return false;
      return true;
    });
    if (!rows.length) {
      out.innerHTML = `<div class="widget-empty">
        <svg class="ico" aria-hidden="true"><use href="#i-package"/></svg>
        <span>No models match the current filters.</span></div>`;
      return;
    }
    const selected = ctx.bus.get().ws_model || '';
    out.innerHTML = `
      <div class="scrollable" style="overflow-x:auto">
        <table class="dash ws-models" style="margin:0;width:100%">
          <thead><tr>
            <th>Model</th><th>Aliases</th><th>Kind</th><th>Tier</th><th>Project</th><th>Registry updated</th>
          </tr></thead>
          <tbody>
            ${rows.map(m => {
              const b = bindingFor(m.name);
              const aliases = Object.entries(m.aliases ?? {});
              return `
              <tr class="ws-model-row${m.name === selected ? ' active' : ''}" data-model="${esc(m.name)}" tabindex="0"
                  title="Show the workbench and monitoring for ${esc(m.name)}">
                <td><strong>${esc(m.name)}</strong></td>
                <td>${aliases.length
                  ? aliases.map(([a, v]) => `<span class="pill ${esc(aliasPillClass(a))}">@${esc(a)} v${esc(v)}</span>`).join(' ')
                  : '<span class="pill neutral">no alias set</span>'}</td>
                <td>${esc(m.deployment_kind)}${m.enabled === false ? ' <span class="pill neutral">paused</span>' : ''}</td>
                <td>${b ? `<span class="art-tier ${esc(b.tier)}">${esc(tierLabel(b.tier))}</span>` : '<span class="muted">n/a</span>'}</td>
                <td>${b?.project_id
                  ? esc(projectName.get(b.project_id) || b.project_id.slice(0, 8) + '…')
                  : '<span class="muted">n/a</span>'}</td>
                <td>${esc(fmtEpochMs(m.last_updated))}</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>
      <p class="muted" style="font-size:.76rem;margin:.4rem 0 0">
        Click a model to open its workbench below: info, input/output schema and a run form.
        ${allowedTiers ? `Tier surface capped to ${esc(allowedTiers.join(', '))} for your role.` : ''}</p>`;

    out.querySelectorAll<HTMLElement>('tr.ws-model-row').forEach(tr => {
      const pick = () => {
        ctx.bus.set('ws_model', tr.dataset.model!);
        out.querySelectorAll('tr.ws-model-row').forEach(x => x.classList.toggle('active', x === tr));
        ctx.host.focusWidget('ws-model-workbench');
      };
      tr.addEventListener('click', pick);
      tr.addEventListener('keydown', (e) => { if ((e as KeyboardEvent).key === 'Enter') pick(); });
      tr.style.cursor = 'pointer';
    });
  };

  ctx.el.querySelector<HTMLSelectElement>('.ws-flt-tier')!
    .addEventListener('change', (e) => { state.tier = (e.currentTarget as HTMLSelectElement).value; paint(); });
  ctx.el.querySelector<HTMLSelectElement>('.ws-flt-project')!
    .addEventListener('change', (e) => { state.project = (e.currentTarget as HTMLSelectElement).value; paint(); });
  ctx.el.querySelector<HTMLInputElement>('.ws-flt-q')!
    .addEventListener('input', (e) => { state.q = (e.currentTarget as HTMLInputElement).value.trim(); paint(); });

  paint();
  void theme;
}

const def: WidgetDef = {
  id: 'ws-models-table',
  reactsTo: [],   // it PUBLISHES ws_model; it doesn't react to it
  render(ctx) { return draw(ctx); },
};
export default def;
