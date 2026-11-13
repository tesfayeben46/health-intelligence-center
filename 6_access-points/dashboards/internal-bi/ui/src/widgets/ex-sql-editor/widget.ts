/** ex-sql-editor: ad-hoc, read-only SQL against the governed marts.
 *
 * POST /gw/sql/execute { sql, limit } runs one read-only statement through
 * Trino with per-referenced-table OPA enforcement, returning
 * { columns, rows, row_count, elapsed_ms, tables_used }. GET /gw/marts lists
 * the bi_% marts for a click-to-insert browser. Errors surface from the
 * server's { detail } (400 = query error, 403 = access denied).
 *
 * Layout: a two-panel top area; a line-numbered, keyword-
 * highlighted editor (~63%) beside an equal-height mart browser (~37%): a
 * controls row, then a full-width results table. The `<textarea data-sql
 * data-widget-input>` is preserved verbatim (the AI assistant + saved-query Run
 * write to it and dispatch `input`, which re-syncs the highlight + gutter). */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import { paginatedTable, type TableColumn } from '../_shared/charts';

const esc = (s: unknown) =>
  String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));

const PLACEHOLDER = 'SELECT * FROM bi_facility.mart_equipment_availability LIMIT 100';
const MIN_LINES = 12;

const KW = /\b(SELECT|FROM|WHERE|LIMIT|GROUP|ORDER|BY|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS|ON|USING|AS|AND|OR|NOT|IN|IS|NULL|LIKE|ILIKE|BETWEEN|DISTINCT|HAVING|UNION|ALL|WITH|CASE|WHEN|THEN|ELSE|END|ASC|DESC|OFFSET|COUNT|SUM|AVG|MIN|MAX|CAST|COALESCE|OVER|PARTITION)\b/gi;

function highlight(sql: string): string {
  // Keyword-only highlight (kept simple/robust: no nested spans). SELECT/LIMIT
  // etc. render blue via .sqlx-kw. A trailing newline keeps the last row's
  // number aligned when the buffer ends without one.
  return esc(sql).replace(KW, '<span class="sqlx-kw">$1</span>') + '\n';
}

const CSS = `
.sqlx { display: flex; flex-direction: column; gap: 1rem; }
.sqlx-top { display: grid; grid-template-columns: minmax(0, 1.72fr) minmax(0, 1fr); gap: 1rem; align-items: stretch; }
.sqlx-editor, .sqlx-marts { min-height: 300px; display: flex; min-width: 0; }
/* Editor: off-white, double outline (outer 1px + inner 1px via inset shadow). */
.sqlx-editor {
  background: #fbfaf7; border: 1px solid #d8dee8; border-radius: 13px;
  box-shadow: inset 0 0 0 1px #eef1f5; overflow: hidden; position: relative;
}
.sqlx-gutter {
  flex: 0 0 auto; width: 2.6rem; padding: 0.7rem 0; overflow: hidden;
  text-align: right; user-select: none;
  font-family: var(--font-mono, ui-monospace, monospace); font-size: 0.82rem; line-height: 1.6;
  color: #b6bdc9; background: transparent; border-right: 1px solid #eef1f5;
}
.sqlx-gutter span { display: block; padding-right: 0.55rem; }
.sqlx-code { position: relative; flex: 1 1 auto; min-width: 0; }
.sqlx-hl, .sqlx-code textarea {
  margin: 0; border: 0; padding: 0.7rem 0.8rem; box-sizing: border-box;
  font-family: var(--font-mono, ui-monospace, monospace); font-size: 0.82rem; line-height: 1.6;
  white-space: pre; overflow: auto; tab-size: 2;
}
.sqlx-hl {
  position: absolute; inset: 0; pointer-events: none; color: #1e293b;
  background: transparent;
}
.sqlx-code textarea {
  position: relative; width: 100%; height: 100%; resize: none;
  background: transparent; color: transparent; caret-color: #142744; outline: none;
}
.sqlx-code textarea::selection { background: rgba(37, 99, 235, 0.18); }
.sqlx-kw { color: #2563eb; font-weight: 600; }
/* Marts browser card. */
.sqlx-marts {
  flex-direction: column; background: #fff; border: 1px solid #e2e8f0; border-radius: 13px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05); padding: 0.75rem 0.8rem; gap: 0.5rem;
}
.sqlx-marts-hd { font-size: 0.82rem; font-weight: 700; color: #1e293b; letter-spacing: -0.01em; }
.sqlx-search {
  display: flex; align-items: center; gap: 0.4rem; padding: 0.35rem 0.55rem;
  border: 1px solid #e2e8f0; border-radius: 9px; background: #f8fafc;
}
.sqlx-search svg { width: 13px; height: 13px; color: #94a3b8; flex: none; }
.sqlx-search input { flex: 1; border: 0; background: transparent; outline: none; font-size: 0.8rem; color: #1e293b; min-width: 0; }
.sqlx-mart-list { flex: 1 1 0; min-height: 0; overflow-y: auto; padding-right: 0.15rem; }
.sqlx-mart-group { font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.05em; color: #94a3b8; font-weight: 700; margin: 0.5rem 0.2rem 0.25rem; }
.sqlx-mart-item {
  display: flex; align-items: center; gap: 0.45rem; width: 100%; text-align: left;
  border: 0; background: none; cursor: pointer; padding: 0.32rem 0.45rem; border-radius: 7px;
  font-family: var(--font-mono, ui-monospace, monospace); font-size: 0.76rem; color: #334155;
}
.sqlx-mart-item:hover { background: #f1f5f9; color: #142744; }
.sqlx-mart-item svg { width: 13px; height: 13px; color: #94a3b8; flex: none; }
/* Slim custom scrollbar. */
.sqlx-mart-list::-webkit-scrollbar, .sqlx-hl::-webkit-scrollbar, .sqlx-code textarea::-webkit-scrollbar { width: 8px; height: 8px; }
.sqlx-mart-list::-webkit-scrollbar-thumb, .sqlx-hl::-webkit-scrollbar-thumb, .sqlx-code textarea::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 8px; }
.sqlx-mart-list::-webkit-scrollbar-track { background: transparent; }
/* Controls. */
.sqlx-controls { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
.sqlx-run {
  display: inline-flex; align-items: center; gap: 0.45rem; border: 0; cursor: pointer;
  background: #142744; color: #fff; font-weight: 600; font-size: 0.83rem;
  padding: 0.5rem 0.95rem; border-radius: 10px; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.12);
}
.sqlx-run:hover { background: #1d3a63; }
.sqlx-run:disabled { opacity: 0.6; cursor: default; }
.sqlx-run svg { width: 13px; height: 13px; }
.sqlx-limit { display: flex; align-items: center; gap: 0.35rem; font-size: 0.8rem; color: #64748b; }
.sqlx-limit input { width: 76px; padding: 0.32rem 0.45rem; border: 1px solid #e2e8f0; border-radius: 8px; font-size: 0.8rem; color: #1e293b; }
.sqlx-help { font-size: 0.76rem; color: #94a3b8; }
/* Results. */
.sqlx-status { display: flex; align-items: center; gap: 0.45rem; font-size: 0.8rem; color: #475569; margin-bottom: 0.4rem; }
.sqlx-status .ok { color: #16a34a; width: 14px; height: 14px; flex: none; }
.sqlx-csv { display: inline-flex; align-items: center; gap: 0.35rem; border: 1px solid #e2e8f0; background: #fff; color: #334155; font-size: 0.76rem; font-weight: 600; padding: 0.32rem 0.62rem; border-radius: 8px; cursor: pointer; }
.sqlx-csv:hover { background: #f1f5f9; color: #142744; }
.sqlx-csv svg { width: 12px; height: 12px; }
.sqlx-results-tbl { border: 1px solid #e2e8f0; border-radius: 12px; overflow: auto; max-height: 460px; }
.sqlx-results-tbl table { width: 100%; border-collapse: collapse; margin: 0; font-size: 0.82rem; }
.sqlx-results-tbl thead th {
  position: sticky; top: 0; background: #f8fafc; color: #475569; font-weight: 600; text-align: left;
  padding: 0.6rem 0.8rem; border-bottom: 1px solid #e2e8f0; white-space: nowrap;
  text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.03em;
}
.sqlx-results-tbl tbody td { padding: 0.62rem 0.8rem; border-bottom: 1px solid #f1f5f9; color: #142744; white-space: nowrap; }
.sqlx-results-tbl tbody tr:last-child td { border-bottom: 0; }
.sqlx-err { margin: 0.35rem 0; padding: 0.6rem 0.75rem; border-radius: 9px; }
`;

function ensureCss() {
  if (!document.getElementById('sqlx-css')) {
    const s = document.createElement('style');
    s.id = 'sqlx-css'; s.textContent = CSS;
    document.head.appendChild(s);
  }
}

const ICON = {
  play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>',
  cube: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2l9 5v10l-9 5-9-5V7z"/><path d="M3 7l9 5 9-5M12 12v10"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M20 6L9 17l-5-5"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0 0l-4-4m4 4l4-4M4 21h16"/></svg>',
  save: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/></svg>',
};

async function loadMarts(listEl: HTMLElement, ta: HTMLTextAreaElement, syncEditor: () => void) {
  let rows: any[] = [];
  try {
    const r = await authFetch('/gw/marts');
    if (r.ok) { const j = await r.json(); rows = Array.isArray(j.rows) ? j.rows : []; }
  } catch { /* leave empty */ }
  if (!rows.length) { listEl.innerHTML = `<p style="font-size:.78rem;color:#94a3b8;padding:.4rem">No marts listed.</p>`; return () => {}; }

  const groups: Record<string, string[]> = {};
  rows.forEach(m => { (groups[m.table_schema] ||= []).push(m.table_name); });
  const render = (filter: string) => {
    const f = filter.trim().toLowerCase();
    listEl.innerHTML = Object.entries(groups).map(([schema, tables]) => {
      const items = tables.filter(t => !f || `${schema}.${t}`.toLowerCase().includes(f)).map(t =>
        `<button class="sqlx-mart-item" data-fq="${esc(schema)}.${esc(t)}" title="${esc(schema)}.${esc(t)}">
           ${ICON.cube}<span>${esc(t)}</span></button>`).join('');
      return items ? `<div class="sqlx-mart-grp"><div class="sqlx-mart-group">${esc(schema.toUpperCase())}</div>${items}</div>` : '';
    }).join('') || `<p style="font-size:.78rem;color:#94a3b8;padding:.4rem">No match.</p>`;
    listEl.querySelectorAll<HTMLButtonElement>('.sqlx-mart-item').forEach(b => {
      b.addEventListener('click', () => {
        const fq = b.dataset.fq!;
        const start = ta.selectionStart ?? ta.value.length;
        ta.setRangeText(fq, start, ta.selectionEnd ?? start, 'end');
        ta.focus(); syncEditor();
      });
    });
  };
  render('');
  return render;
}

function toCsv(cols: string[], rows: any[]): string {
  const cell = (v: any) => {
    const s = v == null ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [cols.map(cell).join(','), ...rows.map(r => cols.map(c => cell(r[c])).join(','))].join('\n');
}

function renderResult(ctx: WidgetCtx, resEl: HTMLElement, data: any) {
  const cols: string[] = data.columns || [];
  const rows: any[] = data.rows || [];
  const n = data.row_count ?? rows.length;
  const meta = `${n} row${n === 1 ? '' : 's'}, ${data.elapsed_ms ?? '-'} ms`
    + (data.tables_used?.length ? `, ${esc(data.tables_used.join(', '))}` : '');
  const status = `<div class="sqlx-status">${ICON.check.replace('<svg', '<svg class="ok"')}<span>${meta}</span>
      <span style="flex:1"></span>
      ${rows.length ? `<button class="sqlx-csv" data-csv type="button">${ICON.download}Export CSV</button>` : ''}
    </div>`;
  if (!rows.length) {
    resEl.innerHTML = `${status}<div style="font-size:.82rem;color:#94a3b8;padding:.3rem 0">No rows returned.</div>`;
    return;
  }
  resEl.innerHTML = `${status}<div data-restbl></div>`;
  // Standard searchable / sortable / paginated data table (the shared one).
  const columns: TableColumn[] = cols.map(c => ({ id: c, label: c }));
  paginatedTable(resEl.querySelector<HTMLElement>('[data-restbl]')!, rows, columns, ctx.theme,
                 { pageSize: 15, search: true, sort: true });
  // CSV export of the full result set (not just the current page).
  resEl.querySelector<HTMLButtonElement>('[data-csv]')?.addEventListener('click', () => {
    const blob = new Blob([toCsv(cols, rows)], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `query-result-${Date.now()}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  });
}

function renderError(resEl: HTMLElement, status: number, detail: string) {
  const denied = status === 403;
  const color = denied ? '#dc2626' : '#d97706';
  resEl.innerHTML = `
    <div class="sqlx-err" style="border:1px solid ${color};background:${color}12">
      <div style="font-size:.8rem;font-weight:700;color:${color}">${denied ? 'Access denied' : 'Query error'}</div>
      <div style="font-size:.82rem;color:#142744;margin-top:.2rem;white-space:pre-wrap">${esc(detail)}</div>
    </div>`;
}

const def: WidgetDef = {
  id: 'ex-sql-editor',
  reactsTo: [],
  render(ctx: WidgetCtx) {
    ensureCss();
    ctx.el.innerHTML = `
      <div class="sqlx">
        <div class="sqlx-top">
          <div class="sqlx-editor">
            <div class="sqlx-gutter" data-gutter></div>
            <div class="sqlx-code">
              <pre class="sqlx-hl" data-hl aria-hidden="true"></pre>
              <textarea data-sql data-widget-input="ex-sql-editor" spellcheck="false"
                placeholder="${PLACEHOLDER}"></textarea>
            </div>
          </div>
          <div class="sqlx-marts">
            <div class="sqlx-marts-hd">Available marts</div>
            <div class="sqlx-search">${ICON.search}<input data-mart-filter type="text" placeholder="Search marts…"/></div>
            <div class="sqlx-mart-list" data-mart-list></div>
          </div>
        </div>
        <div class="sqlx-controls">
          <button class="sqlx-run" data-run type="button">${ICON.play}Run query</button>
          <label class="sqlx-limit">limit <input data-limit type="number" value="500" min="1" max="5000"/></label>
          <button class="sqlx-csv" data-save-tpl type="button">${ICON.save}Save as template</button>
          <span class="sqlx-help">read-only, OPA-enforced per table, ⌘/Ctrl+Enter to run</span>
        </div>
        <div data-result></div>
      </div>`;

    const ta = ctx.el.querySelector<HTMLTextAreaElement>('[data-sql]')!;
    const hl = ctx.el.querySelector<HTMLPreElement>('[data-hl]')!;
    const gutter = ctx.el.querySelector<HTMLElement>('[data-gutter]')!;
    const resEl = ctx.el.querySelector<HTMLElement>('[data-result]')!;
    const listEl = ctx.el.querySelector<HTMLElement>('[data-mart-list]')!;
    const filterEl = ctx.el.querySelector<HTMLInputElement>('[data-mart-filter]')!;
    const runBtn = ctx.el.querySelector<HTMLButtonElement>('[data-run]')!;
    const limitEl = ctx.el.querySelector<HTMLInputElement>('[data-limit]')!;

    const syncEditor = () => {
      hl.innerHTML = highlight(ta.value);
      const lines = Math.max(MIN_LINES, ta.value.split('\n').length);
      let g = '';
      for (let i = 1; i <= lines; i++) g += `<span>${i}</span>`;
      gutter.innerHTML = g;
      hl.scrollTop = ta.scrollTop; hl.scrollLeft = ta.scrollLeft;
      gutter.scrollTop = ta.scrollTop;
    };
    ta.addEventListener('input', syncEditor);   // fires on typing and on the assistant's programmatic write
    ta.addEventListener('scroll', () => { hl.scrollTop = ta.scrollTop; hl.scrollLeft = ta.scrollLeft; gutter.scrollTop = ta.scrollTop; });
    syncEditor();

    let rerenderList: ((f: string) => void) | undefined;
    loadMarts(listEl, ta, syncEditor).then(fn => { rerenderList = fn; });
    filterEl.addEventListener('input', () => rerenderList?.(filterEl.value));

    async function run() {
      const sql = ta.value.trim() || PLACEHOLDER;
      const limit = Math.max(1, Math.min(5000, Number(limitEl.value) || 500));
      runBtn.disabled = true;
      resEl.innerHTML = `<div style="font-size:.82rem;color:#94a3b8;padding:.4rem 0">Running…</div>`;
      try {
        const r = await authFetch('/gw/sql/execute', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sql, limit }),
        });
        const j = await r.json().catch(() => ({}));
        if (r.ok) renderResult(ctx, resEl, j);
        else renderError(resEl, r.status, j.detail || `Request failed (${r.status}).`);
      } catch (e: any) {
        renderError(resEl, 0, e?.message || 'Network error.');
      } finally { runBtn.disabled = false; }
    }
    runBtn.addEventListener('click', run);
    ta.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); run(); }
    });

    // Save the current query as a REUSABLE template (your own, editable set: not
    // just the fixed starters). It lands in Saved queries via /gw/queries.
    const saveBtn = ctx.el.querySelector<HTMLButtonElement>('[data-save-tpl]');
    saveBtn?.addEventListener('click', async () => {
      const sql = ta.value.trim();
      if (!sql) return;
      const title = window.prompt('Save this query as a template. Name it:');
      if (!title || title.trim().length < 2) return;
      const orig = saveBtn.innerHTML;
      saveBtn.disabled = true;
      try {
        const r = await authFetch('/gw/queries', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: title.trim(), sql_text: sql, mart_scope: [] }),
        });
        saveBtn.innerHTML = r.ok ? `${ICON.check} Saved` : 'Save failed';
      } catch { saveBtn.innerHTML = 'Save failed'; }
      setTimeout(() => { saveBtn.innerHTML = orig; saveBtn.disabled = false; }, 1800);
    });
  },
};
export default def;
