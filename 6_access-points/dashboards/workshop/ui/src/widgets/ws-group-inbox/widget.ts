/** ws-group-inbox: things awaiting YOUR action:
 *   · invitations sent to you to accept / decline
 *   · join-requests to groups you own to approve / decline
 *  GET /gw/groups/invites/received · GET /gw/groups/requests/incoming
 *  POST /gw/groups/invites/{id}/accept|decline · /gw/groups/requests/{id}/approve|decline */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import { esc, errText, toast, relTime } from '../_shared/helpers';

type Invite = { id: string; group_name: string; invited_by: string; message: string | null; created_at: string };
type Req = { id: string; group_name: string; requester: string; message: string | null; created_at: string };

async function jsonOf(path: string): Promise<any> { try { const r = await authFetch(path); return r.ok ? await r.json() : null; } catch { return null; } }

async function draw(ctx: WidgetCtx): Promise<void> {
  const { theme } = ctx;
  const [inv, inc] = await Promise.all([jsonOf('/gw/groups/invites/received'), jsonOf('/gw/groups/requests/incoming')]);
  const invites: Invite[] = inv?.invites || [];
  const requests: Req[] = inc?.requests || [];

  if (!invites.length && !requests.length) {
    ctx.el.innerHTML = `<div style="padding:1.2rem;text-align:center;color:${theme.ink[2]};font-size:.85rem">
      Nothing needs your attention: no pending invitations or join-requests.</div>`;
    return;
  }
  const row = (icon: string, title: string, sub: string, meta: string, actions: string) => `
    <div class="ws-cm-row" style="display:flex;align-items:center;gap:.7rem;padding:.6rem .2rem">
      <svg class="ico ico-sm" style="opacity:.6"><use href="#${icon}"/></svg>
      <div style="flex:1;min-width:0">
        <div style="font-size:.88rem;color:${theme.ink[0]}">${title}</div>
        <div style="font-size:.75rem;color:${theme.ink[2]}">${sub}${meta ? ` · ${meta}` : ''}</div>
      </div>${actions}
    </div>`;

  const invHtml = invites.map(i => row('i-users',
    `Invitation to <strong>${esc(i.group_name)}</strong>`,
    `from ${esc(i.invited_by || 'an owner')}${i.message ? `: "${esc(i.message)}"` : ''}`,
    relTime(i.created_at),
    `<button class="btn btn-sm btn-primary" data-act="inv-accept" data-id="${esc(i.id)}">Accept</button>
     <button class="btn btn-sm btn-ghost" data-act="inv-decline" data-id="${esc(i.id)}">Decline</button>`)).join('');

  const reqHtml = requests.map(r => row('i-arrow-right',
    `<strong>${esc(r.requester || 'A user')}</strong> wants to join <strong>${esc(r.group_name)}</strong>`,
    r.message ? `"${esc(r.message)}"` : 'no message',
    relTime(r.created_at),
    `<button class="btn btn-sm btn-primary" data-act="req-approve" data-id="${esc(r.id)}">Approve</button>
     <button class="btn btn-sm btn-ghost" data-act="req-decline" data-id="${esc(r.id)}">Decline</button>`)).join('');

  ctx.el.innerHTML = `
    ${invites.length ? `<div style="font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:${theme.ink[2]};font-weight:600;margin:.2rem 0 .3rem">Invitations to you (${invites.length})</div>${invHtml}` : ''}
    ${requests.length ? `<div style="font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:${theme.ink[2]};font-weight:600;margin:.7rem 0 .3rem">Requests to your groups (${requests.length})</div>${reqHtml}` : ''}`;

  const act = async (path: string, ok: string) => {
    try { const r = await authFetch(path, { method: 'POST' }); if (!r.ok) { toast(await errText(r)); return; } toast(ok); await draw(ctx); }
    catch (e: any) { toast(e?.message || String(e)); }
  };
  ctx.el.querySelectorAll<HTMLButtonElement>('button[data-act]').forEach(b => {
    const id = b.dataset.id!, a = b.dataset.act!;
    b.addEventListener('click', () => {
      if (a === 'inv-accept') act(`/gw/groups/invites/${id}/accept`, 'Joined the group');
      else if (a === 'inv-decline') act(`/gw/groups/invites/${id}/decline`, 'Invitation declined');
      else if (a === 'req-approve') act(`/gw/groups/requests/${id}/approve`, 'Request approved');
      else if (a === 'req-decline') act(`/gw/groups/requests/${id}/decline`, 'Request declined');
    });
  });
}

const def: WidgetDef = { id: 'ws-group-inbox', reactsTo: [], render(ctx) { return draw(ctx); } };
export default def;
