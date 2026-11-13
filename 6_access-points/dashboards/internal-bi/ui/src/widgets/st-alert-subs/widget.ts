/** st-alert-subs: the signed-in user's alert subscriptions.
 *
 * GET /gw/alerts/subscriptions lists the caller's own subscriptions (owner =
 * kc_sub); pause/resume via PATCH, remove via DELETE. New ones are created from
 * a disease widget's Subscribe button or the top-bar bell (the AlertModal), with
 * the scope pre-filled from where the user clicked, so creation lives in context
 * rather than in Settings. Explicit empty state when the user has none. */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import { DISEASE_NAME, scopeLabels } from '../../biApp';

const esc = (s: unknown) =>
  String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));

const diseaseLabel = (k: string) => (k === 'all' || !k) ? 'All diseases' : ((DISEASE_NAME as any)[k] || k);
const cadenceLabel = (k: string) => (({ realtime: 'Real-time', daily: 'Daily digest', weekly: 'Weekly' } as any)[k] || k);

async function draw(ctx: WidgetCtx) {
  const { theme } = ctx;
  let subs: any[];
  try {
    const r = await authFetch('/gw/alerts/subscriptions');
    if (!r.ok) { ctx.empty('Could not load your alert subscriptions.'); return; }
    subs = await r.json();
  } catch { ctx.empty('Could not load your alert subscriptions.'); return; }

  if (!Array.isArray(subs) || !subs.length) {
    ctx.empty('No alert subscriptions yet. Use the Subscribe button on any disease/programme widget (or the 🔔 in the top bar) to be emailed when alarm probability crosses your threshold.');
    return;
  }

  const rows = subs.map(s => {
    const paused = s.status === 'paused';
    const dot = paused ? theme.ink[2] : theme.status.good;
    return `<tr data-id="${esc(s.id)}" style="opacity:${paused ? '.6' : '1'}">
      <td><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${dot};margin-right:.4rem"></span>${esc(diseaseLabel(s.disease))}</td>
      <td>${s.geography === 'all' ? esc(scopeLabels.allGeography) : esc(s.geography)}</td>
      <td class="num">>= ${((s.threshold ?? 0) * 100).toFixed(0)}%</td>
      <td>${esc(cadenceLabel(s.cadence))}</td>
      <td>${s.fire_count ? `<span class="pill info">fired ${s.fire_count} times</span>` : '<span class="pill">n/a</span>'}</td>
      <td style="white-space:nowrap">
        <button class="btn btn-sm btn-ghost" data-act="toggle" data-id="${esc(s.id)}" data-st="${esc(s.status)}">${paused ? 'Resume' : 'Pause'}</button>
        <button class="btn btn-sm btn-ghost" data-act="del" data-id="${esc(s.id)}">Remove</button>
      </td>
    </tr>`;
  }).join('');

  ctx.el.innerHTML = `
    <p style="margin:0 0 .5rem;font-size:.84rem;color:${theme.ink[2]}">
      You’ll be emailed at your account address when a subscription fires. Create new alerts from a
      disease widget’s <strong>Subscribe</strong> button (scope pre-fills from context).
    </p>
    <div class="scrollable" style="overflow-x:auto">
      <table class="dash" style="margin:0;width:100%">
        <thead><tr><th>Disease</th><th>Geography</th><th class="num">Threshold</th><th>Frequency</th><th>Activity</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;

  ctx.el.querySelectorAll<HTMLButtonElement>('button[data-act]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.id!;
      btn.disabled = true;
      try {
        if (btn.dataset.act === 'del') {
          if (!window.confirm('Remove this alert subscription?')) { btn.disabled = false; return; }
          await authFetch(`/gw/alerts/subscriptions/${encodeURIComponent(id)}`, { method: 'DELETE' });
        } else {
          const next = btn.dataset.st === 'active' ? 'paused' : 'active';
          await authFetch(`/gw/alerts/subscriptions/${encodeURIComponent(id)}`, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: next }),
          });
        }
      } catch { /* reflected by the unchanged row after redraw */ }
      finally {
        // keep the topbar bell badge in sync with the change
        (window as any).__biApp?.loadAlertSubs?.();
        await draw(ctx);
      }
    });
  });
}

const def: WidgetDef = {
  id: 'st-alert-subs',
  reactsTo: [],
  render(ctx) { return draw(ctx); },
};
export default def;
