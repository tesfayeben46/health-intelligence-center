/** ws-stats: the Workspace landing strip, a personal greeting and a compact row
 *  of live metrics about the user and the wider warehouse/community.
 *
 * Every number comes from a gateway endpoint (no synthetic values), counted
 * client-side from the same lists the other widgets already use, so no
 * new gateway route is needed:
 *   GET /auth/me: who is signed in
 *   GET /gw/projects: my projects (owner==me) + public projects
 *   GET /gw/datasets: datasets in the catalogue
 *   GET /gw/ml: models in the catalogue
 *   GET /gw/groups: my group memberships
 *   GET /gw/workspace/servers: my running labs
 * A source that fails degrades to a dash for its cards only; the strip still renders. */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import { esc, unwrapList } from '../_shared/helpers';

type Stat = {
  label: string; value: number | null; icon: string; hint: string;
  group: 'you' | 'warehouse';
};

async function jsonOf(path: string): Promise<any | null> {
  try { const r = await authFetch(path); return r.ok ? await r.json() : null; }
  catch { return null; }
}

async function draw(ctx: WidgetCtx): Promise<void> {
  const [me, projJ, dsJ, mlJ, grpJ, labJ] = await Promise.all([
    jsonOf('/auth/me'),
    jsonOf('/gw/projects'),
    jsonOf('/gw/datasets'),
    jsonOf('/gw/ml'),
    jsonOf('/gw/groups'),
    jsonOf('/gw/workspace/servers'),
  ]);

  const mySub = me?.kc_sub || '';
  const name = me?.full_name || me?.username || 'there';
  const firstName = String(name).trim().split(/\s+/)[0];

  const projects = projJ ? unwrapList<any>(projJ, 'projects', 'items') : null;
  const datasets = dsJ ? unwrapList<any>(dsJ, 'datasets', 'items') : null;
  const models = mlJ ? unwrapList<any>(mlJ, 'models', 'items') : null;
  const groups = grpJ ? unwrapList<any>(grpJ, 'groups', 'items') : null;
  const labs = labJ ? (labJ.labs || []) : null;

  const myProjects = projects ? projects.filter(p => String(p.owner_kc_sub || '') === mySub).length : null;
  const publicProjects = projects ? projects.filter(p => p.visibility === 'public').length : null;
  const myGroups = groups ? groups.filter(g => g.i_am_a_member).length : null;
  const runningLabs = labs ? labs.filter((l: any) => l.ready || l.pending === 'spawn').length : null;

  const stats: Stat[] = [
    { label: 'My projects',        value: myProjects,        icon: 'i-folder',   hint: 'projects you own',            group: 'you' },
    { label: 'Running labs',       value: runningLabs,       icon: 'i-server',   hint: 'your live JupyterLab pods',   group: 'you' },
    { label: 'My groups',          value: myGroups,          icon: 'i-users',    hint: 'groups you belong to',        group: 'you' },
    { label: 'Public projects',    value: publicProjects,    icon: 'i-globe',    hint: 'shared across the community',  group: 'warehouse' },
    { label: 'Datasets',           value: datasets ? datasets.length : null, icon: 'i-database', hint: 'in the catalogue',    group: 'warehouse' },
    { label: 'Models',             value: models ? models.length : null,     icon: 'i-pulse',    hint: 'served in the registry', group: 'warehouse' },
  ];

  const card = (s: Stat) => `
    <div class="ws-stat" title="${esc(s.hint)}">
      <svg class="ico ws-stat-ico"><use href="#${s.icon}"/></svg>
      <div class="ws-stat-num">${s.value == null ? 'n/a' : s.value}</div>
      <div class="ws-stat-label">${esc(s.label)}</div>
    </div>`;

  const cluster = (label: string, group: 'you' | 'warehouse') => `
    <div class="ws-stat-cluster">
      <div class="ws-stat-cluster-hd">${label}</div>
      <div class="ws-stats-grid">${stats.filter(s => s.group === group).map(card).join('')}</div>
    </div>`;

  ctx.el.innerHTML = `
    <div class="ws-stats-wrap">
      <div class="ws-greeting">
        <div class="ws-greeting-hi">Welcome back, ${esc(firstName)}.</div>
        <div class="ws-greeting-sub">Pick up a project, open a lab, or explore the catalogue.</div>
      </div>
      <div class="ws-stat-clusters">
        ${cluster('Your work', 'you')}
        ${cluster('In the warehouse', 'warehouse')}
      </div>
    </div>`;
}

const def: WidgetDef = {
  id: 'ws-stats',
  reactsTo: [],
  render(ctx) { return draw(ctx); },
};
export default def;
