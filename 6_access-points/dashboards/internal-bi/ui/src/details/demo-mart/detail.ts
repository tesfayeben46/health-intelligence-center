/**
 * demo-mart: REFERENCE detail view for the app-shell runtime.
 *
 * Registered under kind 'demo-mart'; opened by demo-appshell-list via
 * host.openDetail('demo-mart', id, { row }). Shows the master-detail contract:
 *   • route.id / route.props: the selected entity + the row handed in
 *   • ctx.fetchEntity: re-reads real data for the detail (here: the
 *                              mart's columns via /gw/sql/execute is overkill,
 *                              so we confirm the row exists in /gw/marts)
 *   • host.closeDetail: "‹ Back" returns to the list
 *   • host.openModal: a modal can be opened from a detail too
 * Not wired to any live page: reachable only via the `#appshell-demo` harness.
 */
import type { DetailDef, DetailCtx } from '../../runtime/types';

const esc = (s: unknown) =>
  String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));

async function draw(ctx: DetailCtx): Promise<void> {
  const { theme, route } = ctx;
  const row = (route.props?.row as Record<string, any>) || {};

  ctx.el.innerHTML = `
    <div class="card card-elev" style="padding:1.25rem 1.4rem">
      <button class="btn btn-sm" data-back style="margin-bottom:.9rem">‹ Back to list</button>
      <div style="font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:${theme.ink[2]}">Mart detail</div>
      <h2 style="margin:.15rem 0 1rem;font-size:1.3rem;color:${theme.ink[0]}"><code>${esc(route.id)}</code></h2>
      <dl style="display:grid;grid-template-columns:auto 1fr;gap:.35rem .9rem;font-size:.9rem;margin:0" data-facts>
        <dt style="color:${theme.ink[2]}">Schema</dt><dd style="margin:0">${esc(row.table_schema ?? 'n/a')}</dd>
        <dt style="color:${theme.ink[2]}">Table</dt><dd style="margin:0">${esc(row.table_name ?? 'n/a')}</dd>
        <dt style="color:${theme.ink[2]}">Status</dt><dd style="margin:0" data-status>checking…</dd>
      </dl>
      <div style="margin-top:1.1rem;display:flex;gap:.5rem">
        <button class="btn btn-sm btn-primary" data-modal>Open info modal</button>
      </div>
    </div>`;

  ctx.el.querySelector<HTMLButtonElement>('[data-back]')!
    .addEventListener('click', () => ctx.host.closeDetail());
  ctx.el.querySelector<HTMLButtonElement>('[data-modal]')!
    .addEventListener('click', () => ctx.host.openModal('demo-info', { title: `Detail · ${route.id}` }));

  // Entity seam again: confirm the selected mart is still present.
  const statusEl = ctx.el.querySelector<HTMLElement>('[data-status]')!;
  const all = await ctx.fetchEntity('/gw/marts');
  const found = !!all?.some(r => `${r.table_schema}.${r.table_name}` === route.id);
  statusEl.textContent = found ? 'confirmed present' : 'no longer visible for your role';
  statusEl.style.color = found ? theme.status.good : theme.status.watch;
}

const def: DetailDef = {
  kind: 'demo-mart',
  render(ctx) { void draw(ctx); },
};
export default def;
