/**
 * demo-info: REFERENCE modal for the app-shell runtime.
 *
 * Registered under id 'demo-info'; opened by demo-appshell-list / demo-mart via
 * host.openModal('demo-info', { title, onDone }). Shows the modal contract:
 *   • ctx.props: open-time props (title, an onDone callback)
 *   • ctx.close(): dismiss (also available: the shell close button or backdrop)
 *   • host.openModal props.onDone: the CALLER owns the post-action refresh, so
 *                        the modal stays decoupled from whatever opened it.
 * The shell draws the backdrop + frame + title; this ModalDef fills the body.
 * Not wired to any live page: reachable only via the `#appshell-demo` harness.
 */
import type { ModalDef, ModalCtx } from '../../runtime/types';

function draw(ctx: ModalCtx): void {
  const { theme, props } = ctx;
  ctx.el.innerHTML = `
    <p style="margin:0 0 1rem;font-size:.92rem;line-height:1.55;color:${theme.ink[1]}">
      This modal is served by the runtime modal host: a widget or detail view
      called <code>host.openModal('demo-info', …)</code> and the shell rendered
      this registered <code>ModalDef</code> in its single modal slot; no shell
      code hardcodes it.
    </p>
    <div style="display:flex;gap:.5rem;justify-content:flex-end">
      <button class="btn btn-sm" data-cancel>Cancel</button>
      <button class="btn btn-sm btn-primary" data-confirm>Confirm &amp; refresh list</button>
    </div>`;

  ctx.el.querySelector<HTMLButtonElement>('[data-cancel]')!
    .addEventListener('click', () => ctx.close());

  ctx.el.querySelector<HTMLButtonElement>('[data-confirm]')!
    .addEventListener('click', () => {
      // Hand control back to whoever opened us, then dismiss.
      if (typeof props.onDone === 'function') { try { props.onDone(); } catch { /* noop */ } }
      ctx.close();
    });
}

const def: ModalDef = {
  id: 'demo-info',
  title: 'App-shell demo',
  render(ctx) { draw(ctx); },
};
export default def;
