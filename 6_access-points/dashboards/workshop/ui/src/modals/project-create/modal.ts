/** project-create: POST /gw/projects {name, description?, visibility}.
 *
 * Creates, atomically and server-side, the project row, its projects/<id>/files/
 * object-store prefix and its MLflow experiment, and returns the new row, which is handed
 * to props.onDone. Only private/public are offered here: the create route
 * takes no group_id: sharing to a group happens afterwards from the
 * project-detail visibility control (PATCH), where the group picker lives.
 * A failed create surfaces the gateway error. */
import type { ModalDef, ModalCtx } from '../../runtime/types';
import { createProject, visibilityHint, type ProjectVisibility } from '../../api/projects';
import { invalidateEntityData } from '../../runtime/dataHook';
import { esc, toast } from '../../widgets/_shared/helpers';

const def: ModalDef = {
  id: 'project-create',
  title: 'New research project',
  render(ctx: ModalCtx) {
    ctx.el.innerHTML = `
      <p class="wk-modal-lede" style="margin-top:0">
        A project gives your work a home: a private file space in the warehouse
        and an experiment for its model runs. You can share it with a group or
        the whole workshop later from the project's visibility control.</p>
      <form class="wk-form" style="display:flex;flex-direction:column;gap:.55rem">
        <label><span>Name</span>
          <input name="name" placeholder="e.g. Malaria early-warning system" required autofocus></label>
        <label><span>Description <span class="muted">· optional</span></span>
          <textarea name="description" rows="3"
            placeholder="What is this project about? What data and models does it use?"></textarea></label>
        <label><span>Visibility</span>
          <select name="visibility">
            <option value="private">Private (only you)</option>
            <option value="public">Public (everyone in the workshop)</option>
          </select></label>
        <p class="muted ws-vis-hint" style="font-size:.8rem;margin:0"></p>
        <div class="ws-create-err"></div>
        <div class="wk-modal-foot" style="padding:.4rem 0 0;background:none;border:none">
          <button type="button" class="btn btn-ghost" data-act="cancel">Cancel</button>
          <button type="submit" class="btn btn-primary">
            <svg class="ico"><use href="#i-plus"/></svg> Create project</button>
        </div>
      </form>`;

    const form = ctx.el.querySelector<HTMLFormElement>('form')!;
    const errBox = form.querySelector<HTMLElement>('.ws-create-err')!;
    const hint = form.querySelector<HTMLElement>('.ws-vis-hint')!;
    const visSel = form.elements.namedItem('visibility') as HTMLSelectElement;
    const setHint = () => {
      hint.textContent =
        `${visibilityHint(visSel.value)} Share with a specific group later from the project detail.`;
    };
    setHint();
    visSel.addEventListener('change', setHint);

    form.querySelector<HTMLButtonElement>('button[data-act="cancel"]')!
      .addEventListener('click', () => ctx.close());

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const name = (form.elements.namedItem('name') as HTMLInputElement).value.trim();
      const description = (form.elements.namedItem('description') as HTMLTextAreaElement).value.trim();
      const visibility = visSel.value as ProjectVisibility;
      if (!name) return;
      const submit = form.querySelector<HTMLButtonElement>('button[type="submit"]')!;
      submit.disabled = true;
      errBox.innerHTML = '';
      try {
        const project = await createProject({
          name,
          ...(description ? { description } : {}),
          visibility,
        });
        invalidateEntityData();
        toast(`Project "${name}" created`);
        ctx.props.onDone?.(project);
        ctx.close();
      } catch (err: any) {
        errBox.innerHTML = `
          <div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">
            Could not create the project: ${esc(err?.message || String(err))}</div>`;
        submit.disabled = false;
      }
    });
  },
};
export default def;
