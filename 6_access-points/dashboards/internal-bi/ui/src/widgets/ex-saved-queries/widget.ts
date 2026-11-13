/** ex-saved-queries: the signed-in user's saved queries.
 *
 * GET /gw/queries/mine (scoped to the caller by kc_sub) lists them. "Run"
 * executes the saved query's sql_text through the shared OPA-gated executor
 * (POST /gw/sql/execute, the same chain the SQL editor uses: SELECT-only,
 * mandatory LIMIT, per-table mart_attrs + OPA) and renders the returned rows
 * inline in a results panel under the table; on a *successful* run it then
 * POSTs /gw/queries/{id}/touch-run to stamp last_run_at (never on failure).
 * DELETE /gw/queries/{id} removes one. Explicit empty state when the user has
 * saved none; errors surface the server's { detail } (403 = denied/row-scoped,
 * 400 = query error). */
import type { WidgetCtx, WidgetDef } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';

const esc = (s: unknown) =>
  String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));

function relTime(iso: string | null): string {
  if (!iso) return 'never';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return esc(iso);
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** Row-count cap for a Run: mirrors sql_lab's DEFAULT_MAX_ROWS. The server
 *  only appends this LIMIT when the saved SQL doesn't already carry one. */
const RUN_LIMIT = 500;

// Result / error renderers are copied from ex-sql-editor: dashboard
// widgets repeat their helpers rather than share a module.
function renderResult(ctx: WidgetCtx, resEl: HTMLElement, data: any) {
  const { theme } = ctx;
  const cols: string[] = data.columns || [];
  const rows: any[] = data.rows || [];
  const n = data.row_count ?? rows.length;
  const meta = `${n} row${n === 1 ? '' : 's'}, ${data.elapsed_ms ?? '-'} ms`
    + (data.tables_used?.length ? `, ${esc(data.tables_used.join(', '))}` : '');
  if (!rows.length) {
    resEl.innerHTML = `<div style="font-size:.82rem;color:${theme.ink[2]};padding:.4rem 0">${meta}, no rows.</div>`;
    return;
  }
  const head = cols.map(c => `<th>${esc(c)}</th>`).join('');
  const body = rows.map(row => `<tr>${cols.map(c => {
    const v = row[c];
    return `<td>${v == null ? '<span style="color:#9a9384">NULL</span>' : esc(v)}</td>`;
  }).join('')}</tr>`).join('');
  resEl.innerHTML = `
    <div style="font-size:.8rem;color:${theme.ink[2]};margin:.35rem 0">${meta}</div>
    <div class="scrollable" style="overflow:auto;max-height:360px">
      <table class="dash sr-table" style="margin:0;width:100%">
        <thead><tr>${head}</tr></thead><tbody>${body}</tbody>
      </table>
    </div>`;
}

function renderError(ctx: WidgetCtx, resEl: HTMLElement, status: number, detail: string) {
  const { theme } = ctx;
  const denied = status === 403;
  const color = denied ? theme.status.alert : theme.status.watch;
  resEl.innerHTML = `
    <div style="margin:.35rem 0;padding:.55rem .7rem;border:1px solid ${color};border-radius:7px;background:${color}12">
      <div style="font-size:.8rem;font-weight:700;color:${color}">${denied ? 'Access denied' : 'Query error'}</div>
      <div style="font-size:.82rem;color:${theme.ink[0]};margin-top:.2rem;white-space:pre-wrap">${esc(detail)}</div>
    </div>`;
}

/** Execute a saved query's SQL through the OPA-gated executor and render the
 *  result (or the error) into `panel`. On success, stamp last_run_at
 *  and reflect it in the row's "Last run" cell. */
async function runQuery(ctx: WidgetCtx, q: any, btn: HTMLButtonElement, panel: HTMLElement) {
  const { theme } = ctx;
  const sql = (q.sql_text ?? '').trim();
  panel.style.display = 'block';
  panel.innerHTML = `
    <div style="font-size:.82rem;font-weight:600;color:${theme.ink[0]};margin:.6rem 0 .1rem">Results: ${esc(q.title)}</div>
    <div data-run-body></div>`;
  const bodyEl = panel.querySelector<HTMLElement>('[data-run-body]')!;

  // Builder-only saved queries (no sql_text) have nothing to hand to the SQL
  // executor: say so plainly rather than firing an empty request.
  if (!sql) {
    bodyEl.innerHTML = `<div style="font-size:.82rem;color:${theme.ink[2]};padding:.4rem 0">This saved query has no SQL text to run.</div>`;
    return;
  }

  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = 'Running…';
  bodyEl.innerHTML = `<div style="font-size:.82rem;color:${theme.ink[2]};padding:.4rem 0">Running…</div>`;
  try {
    const r = await authFetch('/gw/sql/execute', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sql, limit: RUN_LIMIT }),
    });
    const j = await r.json().catch(() => ({}));
    if (r.ok) {
      renderResult(ctx, bodyEl, j);
      // Only stamp last_run_at once the query has run successfully.
      try {
        const tr = await authFetch(`/gw/queries/${encodeURIComponent(q.id)}/touch-run`, { method: 'POST' });
        if (tr.ok) {
          const stamp = await tr.json().catch(() => null);
          const cell = btn.closest('tr')?.querySelector<HTMLElement>('td:nth-child(4)');
          if (cell) {
            const iso = stamp?.last_run_at ?? null;
            cell.textContent = relTime(iso);
            cell.title = iso ?? '';
          }
        }
      } catch { /* the run itself succeeded; a failed timestamp bump is cosmetic */ }
    } else {
      renderError(ctx, bodyEl, r.status, j.detail || `Request failed (${r.status}).`);
    }
  } catch (e: any) {
    renderError(ctx, bodyEl, 0, e?.message || 'Network error.');
  } finally {
    btn.disabled = false;
    btn.textContent = prev;
  }
}

async function draw(ctx: WidgetCtx) {
  const { theme } = ctx;
  let rows: any[];
  try {
    const r = await authFetch('/gw/queries/mine?limit=100');
    if (!r.ok) { ctx.empty('Could not load your saved queries.'); return; }
    rows = await r.json();
  } catch { ctx.empty('Could not load your saved queries.'); return; }
  if (!Array.isArray(rows) || !rows.length) {
    ctx.empty('No saved queries yet. Save one from the SQL editor to re-run or schedule it.');
    return;
  }

  const sharePill: Record<string, string> = {
    private: theme.ink[2], shared: theme.status.info, public: theme.status.good,
  };
  const body = rows.map(q => {
    const desc = q.description ? `<div style="font-size:.78rem;color:${theme.ink[2]};margin-top:.15rem">${esc(q.description)}</div>` : '';
    const scope = Array.isArray(q.mart_scope) && q.mart_scope.length
      ? `<code style="font-size:.78rem">${esc(q.mart_scope.join(', '))}</code>` : '<span style="color:#9a9384">n/a</span>';
    const sched = q.schedule_cron ? `<span class="pill info">${esc(q.schedule_cron)}</span>` : '<span class="pill">manual</span>';
    const sc = sharePill[q.share_scope] || theme.ink[2];
    return `<tr data-qid="${esc(q.id)}">
      <td><strong>${esc(q.title)}</strong>${desc}</td>
      <td>${scope}</td>
      <td><span style="color:${sc};font-weight:600;text-transform:capitalize">${esc(q.share_scope)}</span></td>
      <td title="${esc(q.last_run_at)}">${relTime(q.last_run_at)}</td>
      <td>${sched}</td>
      <td style="white-space:nowrap">
        <button class="btn btn-sm btn-primary" data-act="run" data-qid="${esc(q.id)}">Run</button>
        <button class="btn btn-sm btn-ghost" data-act="del" data-qid="${esc(q.id)}">Delete</button>
      </td>
    </tr>`;
  }).join('');

  ctx.el.innerHTML = `
    <div class="scrollable" style="overflow-x:auto">
      <table class="dash" style="margin:0;width:100%">
        <thead><tr>
          <th>Name</th><th>Datasets</th><th>Sharing</th><th>Last run</th><th>Schedule</th><th></th>
        </tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>
    <div data-run-panel style="display:none;margin-top:.5rem"></div>`;

  const byId = new Map<string, any>(rows.map(q => [String(q.id), q]));
  const panel = ctx.el.querySelector<HTMLElement>('[data-run-panel]')!;

  // Run executes the saved SQL and renders inline: no full redraw (that would
  // wipe the results). Delete mutates the list, so it re-fetches + re-renders.
  ctx.el.querySelectorAll<HTMLButtonElement>('button[data-act="run"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const q = byId.get(btn.dataset.qid!);
      if (q) runQuery(ctx, q, btn, panel);
    });
  });
  ctx.el.querySelectorAll<HTMLButtonElement>('button[data-act="del"]').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!window.confirm('Delete this saved query? This cannot be undone.')) return;
      btn.disabled = true;
      try {
        await authFetch(`/gw/queries/${encodeURIComponent(btn.dataset.qid!)}`, { method: 'DELETE' });
      } catch { /* failure is reflected by the unchanged list after re-draw */ }
      finally {
        await draw(ctx);   // re-fetch + re-render to reflect the removal
      }
    });
  });
}

const def: WidgetDef = {
  id: 'ex-saved-queries',
  reactsTo: [],
  render(ctx) { return draw(ctx); },
};
export default def;
