/** ws-my-groups: the groups you belong to (leave from here). Membership is
 *  invite/request gated: you reached these by accepting an invite or having a
 *  join-request approved. GET /gw/groups (i_am_a_member) · POST /gw/groups/{id}/leave */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import { esc, errText, toast, unwrapList } from '../_shared/helpers';

type GroupRow = { id: string; name: string; description: string; member_count: number; artifact_count: number; i_am_a_member: boolean };

async function draw(ctx: WidgetCtx): Promise<void> {
  const { theme } = ctx;
  let mine: GroupRow[] = [];
  try {
    const r = await authFetch('/gw/groups');
    if (r.ok) mine = unwrapList<GroupRow>(await r.json(), 'groups').filter(g => g.i_am_a_member);
  } catch { /* empty */ }

  if (!mine.length) {
    ctx.el.innerHTML = `<div style="padding:1.2rem;text-align:center;color:${theme.ink[2]};font-size:.85rem">
      You're not in any groups yet. Request to join one from <strong>Discover</strong>, or accept an invitation.</div>`;
    return;
  }
  ctx.el.innerHTML = `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:.7rem">
    ${mine.map(g => `
      <div class="grp-card" data-gid="${esc(g.id)}">
        <div class="grp-row1"><span class="grp-chip"><svg class="ico"><use href="#i-users"/></svg></span><span class="grp-name">${esc(g.name)}</span><span class="grp-badge member">Member</span></div>
        <p class="grp-desc">${esc(g.description || '')}</p>
        <div class="grp-stats"><span>${g.member_count} member${g.member_count === 1 ? '' : 's'}</span>${g.artifact_count > 0 ? `<span>${g.artifact_count} shared</span>` : ''}</div>
        <button class="btn btn-sm btn-ghost mt-sm" data-act="leave" data-gid="${esc(g.id)}"><svg class="ico ico-sm"><use href="#i-x"/></svg> Leave</button>
      </div>`).join('')}
  </div>`;

  ctx.el.querySelectorAll<HTMLButtonElement>('button[data-act="leave"]').forEach(btn => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        const r = await authFetch(`/gw/groups/${encodeURIComponent(btn.dataset.gid!)}/leave`, { method: 'POST' });
        if (!r.ok) { toast(`Could not leave: ${await errText(r)}`); btn.disabled = false; return; }
        toast('Left the group'); await draw(ctx);
      } catch (e: any) { toast(`Could not leave: ${e?.message || e}`); btn.disabled = false; }
    });
  });
}

const def: WidgetDef = { id: 'ws-my-groups', reactsTo: [], render(ctx) { return draw(ctx); } };
export default def;
