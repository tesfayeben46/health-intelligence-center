/** ex-templates: governed report generator.
 *
 * GET /gw/reports/templates lists the system + owned report templates; POST
 * /gw/reports/generate runs the OPA-gated, scope-enforced export (the same
 * mart-export contract, so a scoped user gets only their rows) and returns a
 * presigned download (XLSX / CSV / HTML). Explicit empty state when a role has
 * none. */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';

const esc = (s: unknown) =>
  String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));

const FORMATS = ['xlsx', 'csv', 'html'];

async function draw(ctx: WidgetCtx) {
  const { theme } = ctx;
  const good = theme.status.good || '#1f7a4f';
  const alert = theme.status.alert || '#d9534f';
  let tpls: any[];
  try {
    const r = await authFetch('/gw/reports/templates');
    if (!r.ok) { ctx.empty('Could not load report templates.'); return; }
    tpls = await r.json();
  } catch { ctx.empty('Could not load report templates.'); return; }
  if (!Array.isArray(tpls) || !tpls.length) {
    ctx.empty('No report templates available for your role. Build a governed extract with the SQL editor below.');
    return;
  }

  const body = tpls.map(t => `
    <tr data-tid="${esc(t.id)}">
      <td><strong>${esc(t.name)}</strong>
        ${t.description ? `<div style="font-size:.78rem;color:${theme.ink[2]};margin-top:.15rem">${esc(t.description)}</div>` : ''}
      </td>
      <td>${t.category ? `<span class="pill info">${esc(t.category)}</span>` : '<span style="color:#9a9384">n/a</span>'}</td>
      <td><code style="font-size:.76rem">${esc(t.mart)}</code></td>
      <td style="white-space:nowrap">
        <select data-fmt="${esc(t.id)}" style="padding:.2rem .35rem;font-size:.8rem">
          ${FORMATS.map(f => `<option value="${f}"${f === t.default_format ? ' selected' : ''}>${f.toUpperCase()}</option>`).join('')}
        </select>
        <button class="btn btn-sm btn-primary" data-act="gen" data-tid="${esc(t.id)}">Generate</button>
      </td>
    </tr>`).join('');

  ctx.el.innerHTML = `
    <div class="scrollable" style="overflow-x:auto">
      <table class="dash" style="margin:0;width:100%">
        <thead><tr><th>Report</th><th>Category</th><th>Dataset</th><th></th></tr></thead>
        <tbody>${body}</tbody>
      </table>
      <div data-status style="margin-top:.55rem;font-size:.82rem;color:${theme.ink[2]};min-height:1.1rem"></div>
    </div>`;

  const status = ctx.el.querySelector<HTMLElement>('[data-status]')!;
  ctx.el.querySelectorAll<HTMLButtonElement>('button[data-act="gen"]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const tid = btn.dataset.tid!;
      const sel = ctx.el.querySelector<HTMLSelectElement>(`select[data-fmt="${tid}"]`);
      const format = sel ? sel.value : undefined;
      const label = btn.textContent;
      btn.disabled = true; btn.textContent = 'Generating…'; status.textContent = '';
      try {
        const r = await authFetch('/gw/reports/generate', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ template_id: tid, format }),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) {
          status.innerHTML = `<span style="color:${alert}">${esc(j.detail || `Failed (${r.status})`)}</span>`;
        } else {
          status.innerHTML = `<span style="color:${good}">Report ready: ${esc(j.name)}, ${esc(j.row_count)} rows, ${esc(String(j.format).toUpperCase())}. Downloading…</span>`;
          if (j.download_url) {
            const a = document.createElement('a');
            a.href = j.download_url; a.rel = 'noopener'; a.download = '';
            document.body.appendChild(a); a.click(); a.remove();
          }
        }
      } catch {
        status.innerHTML = `<span style="color:${alert}">Network error generating the report.</span>`;
      } finally {
        btn.disabled = false; btn.textContent = label;
      }
    });
  });
}

const def: WidgetDef = {
  id: 'ex-templates',
  reactsTo: [],
  render(ctx) { void draw(ctx); },
};
export default def;
