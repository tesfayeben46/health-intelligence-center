/** compute-request: the resource-request flow (ported ComputeModal).
 *
 *   GET  /gw/workspace/profiles          the profile catalogue + eligibility
 *   POST /gw/workspace/resource-request  {requested_profile, justification}
 *                                        ops_governance.resource_request
 *
 * The profile list comes from the gateway (profiles.yaml), never a hardcoded
 * spec table; a failed submit surfaces the gateway error. */
import type { ModalDef, ModalCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import { esc, errText, toast } from '../../widgets/_shared/helpers';

type Profile = { slug: string; display_name: string; description: string; self_serve: boolean };
type Catalogue = { default_profile: string | null; profiles: Profile[]; user_role: string | null };

const def: ModalDef = {
  id: 'compute-request',
  title: 'Upgrade your compute',
  async render(ctx: ModalCtx) {
    ctx.el.innerHTML = `<p class="muted">Loading profiles…</p>`;

    let cat: Catalogue | null = null;
    let loadErr = '';
    try {
      const r = await authFetch('/gw/workspace/profiles');
      if (r.ok) cat = await r.json();
      else loadErr = await errText(r);
    } catch (e: any) { loadErr = e?.message || String(e); }

    const profiles = cat?.profiles ?? [];
    const noProfiles = cat !== null && profiles.length === 0;
    // Preselect the first profile you can't self-serve: that's what the
    // request flow exists for: falling back to the first entry.
    const preselect = (profiles.find(p => !p.self_serve) ?? profiles[0])?.slug ?? '';

    ctx.el.innerHTML = `
      <p class="wk-modal-lede" style="margin-top:0">
        Requests land in the operations queue (reviewed by R-OPS-ADM). Once
        approved, your next workspace spawn uses the granted profile.</p>
      ${loadErr ? `
        <div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">
          Failed to load the profile catalogue: ${esc(loadErr)}</div>` : ''}
      ${noProfiles ? `
        <div class="alert-banner" style="background:var(--watch-50);color:var(--watch)">
          The gateway returned no workspace profiles (profile catalogue not
          mounted). Requests can't be submitted until it is.</div>` : ''}
      <form class="wk-form" style="display:flex;flex-direction:column;gap:.55rem">
        <label><span>Profile</span>
          <select name="profile" ${(!cat || noProfiles) ? 'disabled' : ''}>
            ${profiles.map(p => `
              <option value="${esc(p.slug)}"${p.slug === preselect ? ' selected' : ''}>
                ${esc(p.display_name)}${p.self_serve ? ' · already self-serve for your role' : ''}
              </option>`).join('')}
          </select></label>
        <p class="muted ws-prof-desc" style="font-size:.8rem;margin:0"></p>
        <label><span>Why · which research project, what training/inference workload</span>
          <textarea name="justification" rows="4"
            placeholder="e.g. training an outbreak-forecast LightGBM model on the EWARS+ dataset with 14-day windowed features"></textarea></label>
        <div class="ws-req-err"></div>
        <div class="wk-modal-foot" style="padding:.4rem 0 0;background:none;border:none">
          <button type="button" class="btn btn-ghost" data-act="cancel">Cancel</button>
          <button type="submit" class="btn btn-primary" ${noProfiles ? 'disabled' : ''}>
            <svg class="ico"><use href="#i-arrow-right"/></svg> Submit request</button>
        </div>
      </form>`;

    const form = ctx.el.querySelector<HTMLFormElement>('form')!;
    const sel = form.elements.namedItem('profile') as HTMLSelectElement;
    const desc = form.querySelector<HTMLElement>('.ws-prof-desc')!;
    const errBox = form.querySelector<HTMLElement>('.ws-req-err')!;
    const setDesc = () => {
      desc.textContent = profiles.find(p => p.slug === sel.value)?.description || '';
    };
    setDesc();
    sel.addEventListener('change', setDesc);

    form.querySelector<HTMLButtonElement>('button[data-act="cancel"]')!
      .addEventListener('click', () => ctx.close());

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const profile = sel.value;
      const justification =
        (form.elements.namedItem('justification') as HTMLTextAreaElement).value.trim();
      if (!profile || !justification) {
        errBox.innerHTML = `<div class="alert-banner" style="background:var(--watch-50);color:var(--watch)">
          Pick a profile and describe the workload; the reviewer needs the justification.</div>`;
        return;
      }
      const submit = form.querySelector<HTMLButtonElement>('button[type="submit"]')!;
      submit.disabled = true;
      errBox.innerHTML = '';
      try {
        const r = await authFetch('/gw/workspace/resource-request', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ requested_profile: profile, justification }),
        });
        if (!r.ok) {
          errBox.innerHTML = `<div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">
            Request failed: ${esc(await errText(r))}</div>`;
          submit.disabled = false;
          return;
        }
        const body = await r.json();
        toast(`Compute request submitted · ${body.requested_profile} · status ${body.status} · id ${body.id}`);
        ctx.props.onDone?.(body);
        ctx.close();
      } catch (err: any) {
        errBox.innerHTML = `<div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">
          Request failed: ${esc(err?.message || String(err))}</div>`;
        submit.disabled = false;
      }
    });
  },
};
export default def;
