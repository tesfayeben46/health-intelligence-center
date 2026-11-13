/** project-submit: submit a project to become public.
 *
 *   POST /gw/graduation/project/{id}/submit
 *     { review_status:'in_review', checklist:[…], all_essentials_present }
 *
 * The unit of review is the project. Submitting runs the automated
 * essential-files check and hands the project to a human reviewer who opens it,
 * runs its notebooks, and approves (to public) or requests changes. Submission is
 * allowed even when essentials are missing: the checklist only warns and the
 * reviewer decides. On success the modal toasts, re-draws the project detail
 * (in_review) via props.onDone, and shows the returned checklist so the owner
 * sees exactly what was found.
 *
 * Auto-registered by the modal glob (src/modals/**​/modal.ts); opened from
 * ws-project-detail via host.openModal('project-submit', {project_id, onDone}).
 * Self-contained per the repo's repetition-over-shared-coupling rule. */
import type { ModalDef, ModalCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import { invalidateEntityData } from '../../runtime/dataHook';
import { esc, errText, toast } from '../../widgets/_shared/helpers';
import type { ChecklistItem } from '../../api/projects';

/** Render one checklist result to a pass/fail pill table (present/missing/optional). */
function checklistTable(items: ChecklistItem[]): string {
  if (!items || items.length === 0) {
    return `<p class="muted" style="margin:.4rem 0">No checklist was returned.</p>`;
  }
  const rows = items.map((it) => {
    const cls = it.present ? 'good' : (it.required ? 'alert' : 'watch');
    const label = it.present ? 'present' : (it.required ? 'missing' : 'optional');
    const matched = (it.matched && it.matched.length)
      ? it.matched.map(m => `<code>${esc(m)}</code>`).join(' ')
      : '<span class="muted">n/a</span>';
    return `<tr>
      <td>${esc(it.requirement)}${it.required ? '' : ' <span class="muted">· optional</span>'}</td>
      <td><span class="pill ${cls}">${label}</span></td>
      <td>${matched}</td>
    </tr>`;
  }).join('');
  return `
    <div class="art-schema" style="margin-top:.5rem">
      <table>
        <thead><tr><th>Requirement</th><th>Result</th><th>Matched files</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

const def: ModalDef = {
  id: 'project-submit',
  title: 'Submit for review',
  render(ctx: ModalCtx) {
    const projectId = String(ctx.props.project_id || '');
    const projectName = String(ctx.props.project_name || 'this project');
    if (!projectId) { ctx.empty('No project selected.'); return; }

    ctx.el.innerHTML = `
      <p class="wk-modal-lede" style="margin-top:0">
        Submitting <strong>${esc(projectName)}</strong> asks a reviewer to make it
        <strong>public</strong>. Review has two parts:</p>
      <ul style="margin:.2rem 0 .6rem 1.1rem;padding:0;color:var(--ink-2);font-size:.9rem">
        <li>an automated <strong>essential-files check</strong> (an inference script, a
            model artifact or notebook, a train script when a model is present, and a
            README / model card), and</li>
        <li>a <strong>human reviewer</strong> who opens the project, runs its notebooks,
            then approves or requests changes.</li>
      </ul>
      <p class="muted" style="font-size:.85rem;margin:0 0 .6rem">
        You can submit even if some essentials are missing; the checklist just warns,
        and the reviewer decides.</p>
      <div class="ws-submit-result"></div>
      <div class="ws-submit-err"></div>
      <div class="wk-modal-foot" style="padding:.4rem 0 0;background:none;border:none">
        <button type="button" class="btn btn-ghost" data-act="cancel">Cancel</button>
        <button type="button" class="btn btn-primary" data-act="submit">
          <svg class="ico"><use href="#i-graduation"/></svg> Submit for review</button>
      </div>`;

    const foot = ctx.el.querySelector<HTMLElement>('.wk-modal-foot')!;
    const resultBox = ctx.el.querySelector<HTMLElement>('.ws-submit-result')!;
    const errBox = ctx.el.querySelector<HTMLElement>('.ws-submit-err')!;
    const submitBtn = ctx.el.querySelector<HTMLButtonElement>('button[data-act="submit"]')!;

    ctx.el.querySelector<HTMLButtonElement>('button[data-act="cancel"]')!
      .addEventListener('click', () => ctx.close());

    submitBtn.addEventListener('click', async () => {
      submitBtn.disabled = true;
      errBox.innerHTML = '';
      try {
        const r = await authFetch(
          `/gw/graduation/project/${encodeURIComponent(projectId)}/submit`,
          { method: 'POST', headers: { 'content-type': 'application/json' } });
        if (!r.ok) {
          errBox.innerHTML = `
            <div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">
              Could not submit: ${esc(await errText(r))}</div>`;
          submitBtn.disabled = false;
          return;
        }
        const res = await r.json();
        const items: ChecklistItem[] = Array.isArray(res.checklist) ? res.checklist : [];
        const ok = !!res.all_essentials_present;

        // Show the outcome + checklist; the submit action is done.
        resultBox.innerHTML = `
          <div class="alert-banner" style="background:var(--good-50);color:var(--good)">
            <span>Submitted. This project is now <strong>in review</strong>. A reviewer will
            open it, run its notebooks, and decide.</span></div>
          ${!ok ? `
            <div class="alert-banner" style="background:var(--watch-50);color:var(--watch);margin-top:.4rem">
              <span>Heads up: some required essentials are <strong>missing</strong>. You can leave
              it for the reviewer, or add them and re-submit after changes.</span></div>` : `
            <div class="muted" style="font-size:.85rem;margin-top:.4rem">All required essentials are present.</div>`}
          <h4 class="art-section-h" style="margin:.7rem 0 0">Essential-files checklist</h4>
          ${checklistTable(items)}`;

        // Collapse the footer to a single Done that closes.
        foot.innerHTML = `<button type="button" class="btn btn-primary" data-act="done">Done</button>`;
        foot.querySelector<HTMLButtonElement>('button[data-act="done"]')!
          .addEventListener('click', () => ctx.close());

        invalidateEntityData();
        toast(`"${projectName}" submitted for review`);
        ctx.props.onDone?.(res);
      } catch (e: any) {
        errBox.innerHTML = `
          <div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">
            Could not submit: ${esc(e?.message || String(e))}</div>`;
        submitBtn.disabled = false;
      }
    });
  },
};
export default def;
