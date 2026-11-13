/** ws-group-sent: things YOU initiated:
 *   · join-requests you sent to other groups   (pending / approved / declined)
 *   · invitations you sent as an owner          (pending, can be revoked)
 *  GET /gw/groups/requests/mine · POST /gw/groups/invites/{id}/revoke */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import { esc, errText, toast, relTime } from '../_shared/helpers';

const STATUS_TONE: Record<string, string> = { pending: 'watch', approved: 'good', accepted: 'good', declined: 'neutral', revoked: 'neutral' };

async function draw(ctx: WidgetCtx): Promise<void> {
  const { theme } = ctx;
  let reqs: any[] = [], sent: any[] = [];
  try { const r = await authFetch('/gw/groups/requests/mine'); if (r.ok) { const j = await r.json(); reqs = j.requests || []; sent = j.invites_sent || []; } } catch { /* empty */ }

  if (!reqs.length && !sent.length) {
    ctx.el.innerHTML = `<div style="padding:1.2rem;text-align:center;color:${theme.ink[2]};font-size:.85rem">
      You haven't sent any join-requests or invitations yet.</div>`;
    return;
  }
  const line = (title: string, sub: string, status: string, actions = '') => `
    <div class="ws-cm-row" style="display:flex;align-items:center;gap:.7rem;padding:.55rem .2rem">
      <div style="flex:1;min-width:0"><div style="font-size:.87rem;color:${theme.ink[0]}">${title}</div><div style="font-size:.74rem;color:${theme.ink[2]}">${sub}</div></div>
      <span class="pill ${STATUS_TONE[status] || 'neutral'}">${esc(status)}</span>${actions}
    </div>`;

  const reqHtml = reqs.map(r => line(`Request to join <strong>${esc(r.group_name)}</strong>`, relTime(r.created_at), r.status)).join('');
  const sentHtml = sent.map(i => line(
    `Invited <strong>${esc(i.invitee || 'a user')}</strong> to <strong>${esc(i.group_name)}</strong>`, relTime(i.created_at), i.status,
    i.status === 'pending' ? `<button class="btn btn-sm btn-ghost" data-act="revoke" data-id="${esc(i.id)}">Revoke</button>` : '')).join('');

  ctx.el.innerHTML = `
    ${reqs.length ? `<div style="font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:${theme.ink[2]};font-weight:600;margin:.2rem 0 .3rem">Your join-requests (${reqs.length})</div>${reqHtml}` : ''}
    ${sent.length ? `<div style="font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:${theme.ink[2]};font-weight:600;margin:.7rem 0 .3rem">Invitations you sent (${sent.length})</div>${sentHtml}` : ''}`;

  ctx.el.querySelectorAll<HTMLButtonElement>('button[data-act="revoke"]').forEach(b => {
    b.addEventListener('click', async () => {
      b.disabled = true;
      try { const r = await authFetch(`/gw/groups/invites/${b.dataset.id}/revoke`, { method: 'POST' }); if (!r.ok) { toast(await errText(r)); b.disabled = false; return; } toast('Invitation revoked'); await draw(ctx); }
      catch (e: any) { toast(e?.message || String(e)); b.disabled = false; }
    });
  });
}

const def: WidgetDef = { id: 'ws-group-sent', reactsTo: [], render(ctx) { return draw(ctx); } };
export default def;
