/** ex-audit-trail: the signed-in user's recent data exports.
 *
 * GET /gw/exports/me: the caller's own export jobs (user-scoped by kc_sub in
 * the gateway). Every ad-hoc or template export is written to ops_export and
 * shown here with its status, dataset, format and size. Explicit empty state
 * when the user has not exported anything yet.
 *
 * Rendered with the shared paginatedTable. The per-row Download
 * uses TableColumn.format:'link' (http(s)-only scheme validation, muted dash when
 * absent); Status is a tone-coloured pill (ready to good, pending to watch, failed to alert). */
import type { WidgetDef } from '../../runtime/types';
import type { TableColumn } from '../_shared/charts';
import { paginatedTable } from '../_shared/charts';
import { authFetch } from '../../api/useAuth';

const esc = (s: unknown) =>
  String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));

function relTime(iso: string | null): string {
  if (!iso) return '-';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return esc(iso);
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
function fmtBytes(n: number | null): string {
  if (n == null) return '-';
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

// ready to good · pending to watch · failed to alert (statuses arrive lowercase from ops_export).
const STATUS_TONE: Record<string, string> = { ready: 'good', pending: 'watch', failed: 'alert' };

const def: WidgetDef = {
  id: 'ex-audit-trail',
  reactsTo: [],
  async render(ctx) {
    let rows: any[];
    try {
      const r = await authFetch('/gw/exports/me?limit=25');
      if (!r.ok) { ctx.empty('Could not load your export history.'); return; }
      rows = await r.json();
    } catch { ctx.empty('Could not load your export history.'); return; }
    if (!Array.isArray(rows) || !rows.length) {
      ctx.empty('No exports yet. Run a query or template export and it will be logged here.');
      return;
    }

    const shaped = rows.map(x => ({
      requested: relTime(x.requested_at),
      dataset: esc(Array.isArray(x.mart_scope) && x.mart_scope.length ? x.mart_scope.join(', ') : '-'),
      format: esc((x.format || '').toUpperCase()),
      rows: x.row_count == null ? '-' : Number(x.row_count).toLocaleString(),
      size: fmtBytes(x.byte_count),
      status: String(x.status ?? '-'),
      download: 'Download',
      download_url: x.download_url,
    }));
    const columns: TableColumn[] = [
      { id: 'requested', label: 'Requested' },
      { id: 'dataset', label: 'Dataset' },
      { id: 'format', label: 'Format' },
      { id: 'rows', label: 'Rows' },
      { id: 'size', label: 'Size' },
      { id: 'status', label: 'Status', format: 'pill', tone: STATUS_TONE },
      { id: 'download', label: '', format: 'link', href: 'download_url' },
    ];
    // pageSize 25 = the fetch limit, so all recent exports stay on one scroll (as before).
    return paginatedTable(ctx.el, shaped, columns, ctx.theme, { pageSize: 25 });
  },
};
export default def;
