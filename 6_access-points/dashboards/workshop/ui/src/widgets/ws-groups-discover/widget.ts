/** ws-groups-discover: every group out there. Joining is invite/request gated:
 *  there is no instant join: a user requests to join and the group owner approves.
 *  GET /gw/groups · POST /gw/groups/{id}/request-join · POST /gw/groups (create) */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import { esc, errText, toast, unwrapList } from '../_shared/helpers';

type GroupRow = { id: string; name: string; description: string; member_count: number; i_am_a_member: boolean };

async function draw(ctx: WidgetCtx): Promise<void> {
  const { theme } = ctx;
  let groups: GroupRow[] = [];
  let sentIds = new Set<string>();
  try {
    const [rg, rq] = await Promise.all([authFetch('/gw/groups'), authFetch('/gw/groups/requests/mine')]);
    if (rg.ok) groups = unwrapList<GroupRow>(await rg.json(), 'groups');
    if (rq.ok) { const j = await rq.json(); (j.requests || []).forEach((x: any) => { if (x.status === 'pending') sentIds.add(x.group_id); }); }
  } catch { /* empty */ }

  const discover = groups.filter(g => !g.i_am_a_member);
  ctx.el.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:.9rem;margin-bottom:.7rem">
      <p style="font-size:.83rem;color:${theme.ink[2]};margin:0;max-width:60ch;line-height:1.45">
        Groups share artifacts at the <strong style="color:${theme.ink[1]}">shared</strong> tier. Joining is
        invite-only. <strong>Request to join</strong> and the owner approves you.</p>
      <button class="btn btn-sm btn-primary" data-act="new" style="flex:none"><svg class="ico ico-sm"><use href="#i-plus"/></svg> Create a group</button>
    </div>
    <form class="grp-create" style="display:none;flex-direction:column;gap:.5rem;border:1px solid var(--line);border-radius:10px;padding:.9rem 1rem;background:var(--paper-2);margin-bottom:.7rem">
      <label style="font-size:.8rem">Name<input name="name" minlength="3" maxlength="80" required placeholder="e.g. Malaria modelling network" style="width:100%;margin-top:.2rem;padding:.4rem .55rem;border:1px solid var(--line);border-radius:8px"></label>
      <label style="font-size:.8rem">Description<input name="description" placeholder="What does this group work on?" style="width:100%;margin-top:.2rem;padding:.4rem .55rem;border:1px solid var(--line);border-radius:8px"></label>
      <div class="grp-create-err"></div>
      <div style="display:flex;gap:.5rem;justify-content:flex-end"><button type="button" class="btn btn-sm btn-ghost" data-act="cancel">Cancel</button><button type="submit" class="btn btn-sm btn-primary">Create</button></div>
    </form>
    ${discover.length ? `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:.7rem">
      ${discover.map(g => {
        const pending = sentIds.has(g.id);
        return `<div class="grp-card discover" data-gid="${esc(g.id)}">
          <div class="grp-row1"><span class="grp-chip"><svg class="ico"><use href="#i-users"/></svg></span><span class="grp-name">${esc(g.name)}</span></div>
          <p class="grp-desc">${esc(g.description || '')}</p>
          <div class="grp-stats"><span>${g.member_count} member${g.member_count === 1 ? '' : 's'}</span></div>
          ${pending
            ? `<button class="btn btn-sm btn-ghost mt-sm" disabled aria-disabled="true"><svg class="ico ico-sm"><use href="#i-clock"/></svg> Requested</button>`
            : `<button class="btn btn-sm btn-ghost mt-sm" data-act="request" data-gid="${esc(g.id)}"><svg class="ico ico-sm"><use href="#i-arrow-right"/></svg> Request to join</button>`}
        </div>`;
      }).join('')}
    </div>` : `<p style="color:${theme.ink[2]};font-size:.84rem">No other groups to discover right now.</p>`}
    <div class="grp-msg"></div>`;

  const form = ctx.el.querySelector<HTMLFormElement>('form.grp-create')!;
  ctx.el.querySelector<HTMLButtonElement>('button[data-act="new"]')!.addEventListener('click', () => { form.style.display = 'flex'; });
  form.querySelector<HTMLButtonElement>('button[data-act="cancel"]')!.addEventListener('click', () => { form.style.display = 'none'; });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = (form.elements.namedItem('name') as HTMLInputElement).value.trim();
    const description = (form.elements.namedItem('description') as HTMLInputElement).value.trim();
    if (name.length < 3) return;
    const errEl = form.querySelector<HTMLElement>('.grp-create-err')!;
    try {
      const r = await authFetch('/gw/groups', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name, ...(description ? { description } : {}) }) });
      if (r.status === 201) { toast(`Group "${name}" created`); await draw(ctx); return; }
      errEl.innerHTML = `<div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">Create failed: ${esc(await errText(r))}</div>`;
    } catch (err: any) { errEl.textContent = `Create failed: ${err?.message || err}`; }
  });

  ctx.el.querySelectorAll<HTMLButtonElement>('button[data-act="request"]').forEach(btn => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        const r = await authFetch(`/gw/groups/${encodeURIComponent(btn.dataset.gid!)}/request-join`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
        if (!r.ok) { toast(`Request failed: ${await errText(r)}`); btn.disabled = false; return; }
        toast('Join request sent; the owner will review it'); await draw(ctx);
      } catch (e: any) { toast(`Request failed: ${e?.message || e}`); btn.disabled = false; }
    });
  });
}

const def: WidgetDef = { id: 'ws-groups-discover', reactsTo: [], render(ctx) { return draw(ctx); } };
export default def;
