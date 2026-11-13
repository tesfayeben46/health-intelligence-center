/** ws-running-labs: the caller's running JupyterLab sessions, one per project.
 *
 *   GET  /gw/workspace/servers   my named servers (labs), folded with project names
 *   POST /gw/workspace/stop      {project_id} to stop that project's lab
 *
 * Labs are one per project (named servers). This widget just lists them: you
 * open a lab from a project (ws-project-detail's "Open in JupyterLab"), never a
 * global button. Clean empty state when nothing is running. When the hub is not
 * configured in this environment it says so instead of showing a row. */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import { esc, errText, relTime, toast } from '../_shared/helpers';

type Lab = {
  project_id: string;
  project_name: string;
  project_slug?: string | null;
  ready: boolean;
  pending?: string | null;       // 'spawn' | 'stop' | null
  last_activity?: string | null;
  url?: string | null;
};

function stateChip(l: Lab): string {
  if (l.pending === 'spawn') return '<span class="pill watch">starting…</span>';
  if (l.pending === 'stop')  return '<span class="pill neutral">stopping…</span>';
  if (l.ready)               return '<span class="pill good">running</span>';
  return '<span class="pill neutral">not ready</span>';
}

async function draw(ctx: WidgetCtx): Promise<void> {
  const { theme } = ctx;
  let labs: Lab[] = [];
  let reason = '';
  try {
    const r = await authFetch('/gw/workspace/servers');
    if (r.ok) {
      const j = await r.json();
      labs = (j.labs || []) as Lab[];
      reason = j.reason || '';
    } else {
      reason = await errText(r);
    }
  } catch (e: any) {
    reason = e?.message || String(e);
  }

  if (!labs.length) {
    // Empty state: distinguishes "hub off in this env" from "none running".
    const msg = /not configured/i.test(reason)
      ? 'JupyterLab is unavailable in this environment.'
      : 'No running JupyterLab sessions.';
    const hint = /not configured/i.test(reason)
      ? ''
      : 'Open a lab from a project (open the project, then "Open in JupyterLab"); it will appear here.';
    ctx.el.innerHTML = `
      <div class="ws-labs-empty">
        <span class="ws-labs-empty-chip"><svg class="ico"><use href="#i-server"/></svg></span>
        <div class="ws-labs-empty-msg">${esc(msg)}</div>
        ${hint ? `<div class="ws-labs-empty-hint">${esc(hint)}</div>` : ''}
      </div>`;
    return;
  }

  const rows = labs.map(l => `
    <div class="ws-runlab-row" data-pid="${esc(l.project_id)}"
         style="display:flex;align-items:center;gap:.7rem;flex-wrap:wrap;
                padding:.6rem .2rem;border-bottom:1px solid var(--line)">
      <svg class="ico ico-sm" style="opacity:.6"><use href="#i-server"/></svg>
      <div style="min-width:12rem;flex:1">
        <div style="font-size:.9rem;font-weight:600;color:${theme.ink[0]}">${esc(l.project_name)}</div>
        <div style="font-size:.75rem;color:${theme.ink[2]}">
          ${l.last_activity ? `active ${esc(relTime(l.last_activity))}` : 'no activity yet'}
        </div>
      </div>
      ${stateChip(l)}
      ${l.ready && l.url
        ? `<a class="btn btn-sm btn-primary" href="${esc(l.url)}" target="_blank" rel="noreferrer">
             <svg class="ico ico-sm"><use href="#i-external"/></svg> Open</a>`
        : `<span class="btn btn-sm btn-ghost" aria-disabled="true" style="opacity:.5;cursor:default">Open</span>`}
      <button class="btn btn-sm btn-ghost" data-act="stop" data-pid="${esc(l.project_id)}"
              ${l.pending === 'stop' ? 'disabled aria-disabled="true"' : ''}>Stop</button>
    </div>`).join('');

  ctx.el.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:.2rem">
      ${rows}
      <div class="ws-runlabs-msg" style="display:none;margin-top:.5rem"></div>
    </div>`;

  const msgEl = ctx.el.querySelector<HTMLElement>('.ws-runlabs-msg')!;
  const fail = (text: string) => {
    msgEl.style.display = '';
    msgEl.innerHTML = `<div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">${esc(text)}</div>`;
  };

  ctx.el.querySelectorAll<HTMLButtonElement>('button[data-act="stop"]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const pid = btn.dataset.pid || '';
      btn.disabled = true;
      try {
        const r = await authFetch('/gw/workspace/stop', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ project_id: pid }),
        });
        if (!r.ok) { fail(`Stop failed: ${await errText(r)}`); btn.disabled = false; return; }
        toast('Lab stopped');
        await draw(ctx);            // re-read the real server list
      } catch (err: any) {
        fail(`Stop failed: ${err?.message || String(err)}`);
        btn.disabled = false;
      }
    });
  });
}

const def: WidgetDef = {
  id: 'ws-running-labs',
  reactsTo: [],
  render(ctx) { return draw(ctx); },
};
export default def;
