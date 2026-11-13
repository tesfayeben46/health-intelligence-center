/** ws-project-artifacts: the datasets/models/notebooks bound to the
 *  selected project. reactsTo `ws_project`.
 *
 *   GET /gw/artifacts?project={id}: visibility-scoped by the gateway;
 *   the project filter narrows, never widens. Click opens the detail modal. */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import { esc, errText, unwrapList, fmtDate, kindIcon, kindLabel, tierLabel } from '../_shared/helpers';

type ArtifactRow = {
  id: string; kind: string; title: string; tier: string;
  peer_review_status: string; updated_at: string; latest_version: number | null;
};

async function draw(ctx: WidgetCtx) {
  const { theme } = ctx;
  const projectId = ctx.filters.ws_project || '';
  if (!projectId) {
    ctx.empty('Select a project above to see the artifacts it owns.');
    return;
  }

  // Two sources: artifacts that BELONG to the project (models/notebooks/…,
  // via artifact.project_id) and datasets LINKED to it (the M:N project_dataset
  // table: datasets are independent). Merge both.
  let items: ArtifactRow[] = [];
  try {
    const [rA, rD] = await Promise.all([
      authFetch(`/gw/artifacts?project=${encodeURIComponent(projectId)}&limit=100`),
      authFetch(`/gw/datasets?project_id=${encodeURIComponent(projectId)}&limit=100`),
    ]);
    if (!rA.ok && !rD.ok) { ctx.empty(`Could not load the project's artifacts: ${await errText(rA)}`); return; }
    if (rA.ok) items = unwrapList<ArtifactRow>(await rA.json());
    if (rD.ok) {
      const ds = unwrapList<any>(await rD.json()).map(d => ({ ...d, kind: 'dataset' } as ArtifactRow));
      items = items.concat(ds);
    }
  } catch { ctx.empty('Could not load the project’s artifacts.'); return; }

  if (!items.length) {
    ctx.empty('No artifacts or datasets in this project yet. Generate a dataset and link it, or register a model into the project.');
    return;
  }

  ctx.el.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:.45rem">
      ${items.map(a => `
        <button type="button" class="ws-proj-art" data-aid="${esc(a.id)}"
                title="Open details">
          <svg class="ico ico-sm"><use href="#${esc(kindIcon(a.kind))}"/></svg>
          <span class="ws-proj-art-main">
            <span class="ws-proj-art-title">${esc(a.title)}</span>
            <span class="ws-proj-art-sub" style="color:${theme.ink[2]}">
              ${esc(kindLabel(a.kind))}${a.latest_version != null ? ` · v${esc(a.latest_version)}` : ''} · updated ${esc(fmtDate(a.updated_at))}
            </span>
          </span>
          <span class="art-tier ${esc(a.tier)}">${esc(tierLabel(a.tier))}</span>
        </button>`).join('')}
    </div>`;

  ctx.el.querySelectorAll<HTMLButtonElement>('button.ws-proj-art').forEach(btn => {
    btn.addEventListener('click', () =>
      ctx.host.openModal('dataset-detail', { artifactId: btn.dataset.aid }));
  });
}

const def: WidgetDef = {
  id: 'ws-project-artifacts',
  reactsTo: ['ws_project'],
  render(ctx) { return draw(ctx); },
};
export default def;
