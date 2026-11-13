/** ws-group-invite: send an invitation to join one of YOUR groups. Owners only:
 *  pick a group you own + a username: the invitee gets it in their inbox and
 *  becomes a member on accept. GET /gw/groups (i_own) · POST /gw/groups/{id}/invite */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import { esc, errText, toast, unwrapList } from '../_shared/helpers';

type GroupRow = { id: string; name: string; i_own: boolean };

async function draw(ctx: WidgetCtx): Promise<void> {
  const { theme } = ctx;
  let owned: GroupRow[] = [];
  try { const r = await authFetch('/gw/groups'); if (r.ok) owned = unwrapList<GroupRow>(await r.json(), 'groups').filter(g => g.i_own); } catch { /* empty */ }

  if (!owned.length) {
    ctx.el.innerHTML = `<div style="padding:1.2rem;text-align:center;color:${theme.ink[2]};font-size:.85rem">
      You don't own any groups yet. Create one in <strong>Discover</strong>, then invite people here.</div>`;
    return;
  }
  ctx.el.innerHTML = `
    <form class="ws-invite-form" style="display:flex;flex-direction:column;gap:.6rem;max-width:34rem">
      <label style="font-size:.82rem;color:${theme.ink[1]}">Group
        <select name="group" style="width:100%;margin-top:.25rem;padding:.45rem .55rem;border:1px solid var(--line);border-radius:8px;background:var(--paper)">
          ${owned.map(g => `<option value="${esc(g.id)}">${esc(g.name)}</option>`).join('')}
        </select></label>
      <label style="font-size:.82rem;color:${theme.ink[1]}">Username to invite
        <input name="username" required placeholder="e.g. genet.e" autocomplete="off" style="width:100%;margin-top:.25rem;padding:.45rem .55rem;border:1px solid var(--line);border-radius:8px"></label>
      <label style="font-size:.82rem;color:${theme.ink[1]}">Message <span class="muted">· optional</span>
        <input name="message" placeholder="Why you'd like them to join" style="width:100%;margin-top:.25rem;padding:.45rem .55rem;border:1px solid var(--line);border-radius:8px"></label>
      <div class="ws-invite-msg"></div>
      <div><button type="submit" class="btn btn-sm btn-primary"><svg class="ico ico-sm"><use href="#i-plus"/></svg> Send invitation</button></div>
    </form>`;

  const form = ctx.el.querySelector<HTMLFormElement>('form.ws-invite-form')!;
  const msg = ctx.el.querySelector<HTMLElement>('.ws-invite-msg')!;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const gid = (form.elements.namedItem('group') as HTMLSelectElement).value;
    const username = (form.elements.namedItem('username') as HTMLInputElement).value.trim();
    const message = (form.elements.namedItem('message') as HTMLInputElement).value.trim();
    if (!gid || !username) return;
    const btn = form.querySelector<HTMLButtonElement>('button[type="submit"]')!;
    btn.disabled = true; msg.innerHTML = '';
    try {
      const r = await authFetch(`/gw/groups/${encodeURIComponent(gid)}/invite`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ username, ...(message ? { message } : {}) }),
      });
      if (r.status === 201) { toast(`Invitation sent to ${username}`); (form.elements.namedItem('username') as HTMLInputElement).value = ''; (form.elements.namedItem('message') as HTMLInputElement).value = ''; }
      else msg.innerHTML = `<div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">${esc(await errText(r))}</div>`;
    } catch (err: any) { msg.innerHTML = `<div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">${esc(err?.message || String(err))}</div>`; }
    finally { btn.disabled = false; }
  });
}

const def: WidgetDef = { id: 'ws-group-invite', reactsTo: [], render(ctx) { return draw(ctx); } };
export default def;
