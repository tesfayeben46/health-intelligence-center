/** dataset-detail: full artifact detail in the shell's modal slot.
 *
 *   GET  /gw/artifacts/{id}           artifact + versions + reviews + metric
 *                                     + liked_by_me (view metric increments
 *                                     server-side on this read)
 *   POST /gw/artifacts/{id}/like      per-user like toggle to {liked, likes}
 *   POST /gw/artifacts/{id}/download  visibility-checked presigned URL:
 *                                     {download_url, filename}; opening it
 *                                     downloads and increments the real
 *                                     downloads metric
 *
 * Uses the .art-* classes; the download button calls the gateway-mediated
 * download endpoint. Works for any artifact kind (the contributions
 * board and project-artifacts list reuse it). */
import type { ModalDef, ModalCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import { invalidateEntityData } from '../../runtime/dataHook';
import { listProjects } from '../../api/projects';
import {
  esc, errText, toast, unwrapList, fmtBytes, fmtCount, fmtDate, kindIcon, kindLabel, tierLabel,
} from '../../widgets/_shared/helpers';

/** A project a dataset is linked to (as returned in datasets list linked_projects). */
type LinkedProject = { id: string; name: string };

function reviewStatusLabel(s: string): string {
  return ({ unreviewed: 'unreviewed', in_review: 'in review',
            approved: 'approved', rejected: 'rejected' } as
    Record<string, string>)[s] || s;
}

const def: ModalDef = {
  id: 'dataset-detail',
  title: 'Artifact',
  async render(ctx: ModalCtx) {
    const artifactId = String(ctx.props.artifactId || '');
    if (!artifactId) { ctx.empty('No artifact selected.'); return; }
    ctx.el.innerHTML = `<p class="muted">Loading…</p>`;

    let data: any = null;
    let loadErr = '';
    try {
      const r = await authFetch(`/gw/artifacts/${encodeURIComponent(artifactId)}`);
      if (r.ok) data = await r.json();
      else loadErr = await errText(r);
    } catch (e: any) { loadErr = e?.message || String(e); }

    if (!data?.artifact) {
      ctx.el.innerHTML = `
        <div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">
          Failed to load artifact: ${esc(loadErr || 'not found or not visible')}</div>`;
      return;
    }

    const a = data.artifact;
    const versions: any[] = data.versions || [];
    const reviews: any[] = data.reviews || [];
    const metric = data.metric || null;
    let liked = !!data.liked_by_me;
    let likes = Number(metric?.likes ?? 0);
    const latest = versions[0];
    const hasBytes = versions.length > 0;

    const likeBtnHtml = () => `
      <svg class="ico ico-sm"><use href="#i-heart"/></svg>
      ${liked ? 'Liked' : 'Like'} · ${esc(fmtCount(likes))}`;

    ctx.el.innerHTML = `
      <div class="art-detail-body" style="padding:0">
        <div style="display:flex;gap:.5rem;align-items:center;margin-bottom:.5rem">
          <span class="art-tier ${esc(a.tier)}">${esc(tierLabel(a.tier))}</span>
          <span class="art-type">
            <svg class="ico"><use href="#${esc(kindIcon(a.kind))}"/></svg>
            <span>${esc(kindLabel(a.kind))}</span>
          </span>
        </div>
        <h1 class="art-detail-title" style="font-size:1.35rem">${esc(a.title)}</h1>
        <div class="art-detail-byline">
          <span><strong>${esc(a.owner_username || 'unknown owner')}</strong></span>
          <span class="art-bullet">·</span>
          <span>Updated ${esc(fmtDate(a.updated_at))}</span>
          <span class="art-bullet">·</span>
          <span>${esc(a.license || 'n/a')}</span>
          <span class="art-bullet">·</span>
          <span>review status: ${esc(reviewStatusLabel(a.peer_review_status))}</span>
        </div>
        ${Array.isArray(a.tags) && a.tags.length ? `
          <div class="art-detail-tags">
            ${a.tags.map((t: string) => `<span class="pill neutral">${esc(t)}</span>`).join('')}
          </div>` : ''}
        ${a.description ? `<div class="art-detail-prose"><p>${esc(a.description)}</p></div>` : ''}

        <div class="art-detail-grid" style="grid-template-columns:1fr;gap:1rem">
          <div class="art-facts">
            <h4>At a glance</h4>
            <dl>
              ${latest ? `<div><dt>Size</dt><dd>${esc(fmtBytes(latest.byte_count))}</dd></div>` : ''}
              ${latest ? `<div><dt>Version</dt><dd>v${esc(latest.version)}${versions.length > 1 ? ` · ${versions.length} versions` : ''}</dd></div>` : ''}
              <div><dt>Created</dt><dd>${esc(fmtDate(a.created_at))}</dd></div>
              ${metric ? `
                <div><dt>Activity</dt>
                  <dd class="ws-metric-line">${esc(fmtCount(metric.downloads))} downloads
                    · ${esc(fmtCount(metric.views))} views
                    · <span class="ws-likes">${esc(fmtCount(likes))}</span> likes</dd></div>` : ''}
              ${reviews.length ? `<div><dt>Reviews</dt><dd>${esc(fmtCount(reviews.length))}</dd></div>` : ''}
              ${a.mlflow_stage ? `
                <div><dt>MLflow</dt><dd>${esc(a.mlflow_name || '')}${a.mlflow_version ? ` v${esc(a.mlflow_version)}` : ''} · ${esc(a.mlflow_stage)}</dd></div>` : ''}
            </dl>
          </div>

          <div class="art-actions" style="display:flex;gap:.5rem;flex-wrap:wrap;align-items:center">
            ${hasBytes
              ? `<button class="btn btn-primary" data-act="download">
                   <svg class="ico"><use href="#i-download"/></svg>
                   <span>Download${latest ? ` v${esc(latest.version)}` : ''}</span></button>`
              : `<button class="btn btn-primary" disabled aria-disabled="true"
                   title="This artifact has no stored version to download">
                   <svg class="ico"><use href="#i-download"/></svg> Download</button>`}
            <button class="btn btn-ghost" data-act="like">${likeBtnHtml()}</button>
          </div>
          <div class="ws-art-msg"></div>

          ${a.kind === 'dataset' ? `
            <section class="art-section ws-dataset-projects" style="margin-top:.4rem">
              <h3 class="art-section-h">Projects</h3>
              <p class="muted" style="font-size:.82rem;margin:.1rem 0 .5rem">
                Linking a dataset to a project copies its CSV into the project's
                file space so labs can read it. You can link to projects you can
                contribute to.</p>
              <div class="ws-dataset-projects-slot"><p class="muted">Loading links…</p></div>
            </section>` : ''}

          ${a.citation ? `
            <p class="art-cite" style="margin:0">
              <strong>Cite</strong><br>
              <code class="art-cite-text">${esc(a.citation)}</code></p>` : ''}

          ${versions.length ? `
            <section class="art-section" style="margin-top:.4rem">
              <h3 class="art-section-h">Versions</h3>
              <div class="art-schema">
                <table>
                  <thead><tr><th>Version</th><th>Size</th><th>Uploaded</th><th>Change note</th></tr></thead>
                  <tbody>
                    ${versions.map((v: any) => `
                      <tr>
                        <td><code>v${esc(v.version)}</code></td>
                        <td class="muted">${esc(fmtBytes(v.byte_count))}</td>
                        <td class="muted">${esc(fmtDate(v.created_at))}</td>
                        <td>${esc(v.change_note || 'n/a')}</td>
                      </tr>`).join('')}
                  </tbody>
                </table>
              </div>
            </section>` : ''}

          ${reviews.length ? `
            <section class="art-section" style="margin-top:.4rem">
              <h3 class="art-section-h">Peer reviews</h3>
              <div class="art-reviews">
                ${reviews.map((r: any) => `
                  <div class="art-review rv-${esc(r.verdict)}">
                    <div class="art-review-hdr">
                      <span class="rv-verdict">${esc(String(r.verdict || '').replace('_', ' '))}</span>
                      <span class="rv-by">${esc(r.reviewer_username || r.reviewer)}</span>
                      <span class="rv-date">${esc(fmtDate(r.reviewed_at))}</span>
                    </div>
                    ${r.note ? `<p class="art-review-note">${esc(r.note)}</p>` : ''}
                  </div>`).join('')}
              </div>
            </section>` : ''}
        </div>
      </div>`;

    const msg = ctx.el.querySelector<HTMLElement>('.ws-art-msg')!;
    const showErr = (text: string) => {
      msg.innerHTML = `<div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">${esc(text)}</div>`;
    };

    // like toggle (ledger-backed, idempotent server-side)
    const likeBtn = ctx.el.querySelector<HTMLButtonElement>('button[data-act="like"]');
    likeBtn?.addEventListener('click', async () => {
      likeBtn.disabled = true;
      try {
        const r = await authFetch(`/gw/artifacts/${encodeURIComponent(artifactId)}/like`, { method: 'POST' });
        if (!r.ok) { showErr(`Could not update the like: ${await errText(r)}`); return; }
        const res = await r.json();
        liked = !!res.liked;
        likes = Number(res.likes ?? likes);
        likeBtn.innerHTML = likeBtnHtml();
        const line = ctx.el.querySelector<HTMLElement>('.ws-likes');
        if (line) line.textContent = fmtCount(likes);
        invalidateEntityData();
        ctx.props.onDone?.();
      } catch (e: any) {
        showErr(`Could not update the like: ${e?.message || String(e)}`);
      } finally {
        likeBtn.disabled = false;
      }
    });

    // real gateway-mediated download (presigned URL + metric increment)
    const dlBtn = ctx.el.querySelector<HTMLButtonElement>('button[data-act="download"]');
    dlBtn?.addEventListener('click', async () => {
      dlBtn.disabled = true;
      try {
        const r = await authFetch(`/gw/artifacts/${encodeURIComponent(artifactId)}/download`, { method: 'POST' });
        if (!r.ok) { showErr(`Download failed: ${await errText(r)}`); return; }
        const res = await r.json();
        if (!res.download_url) { showErr('The gateway returned no download URL.'); return; }
        window.open(res.download_url, '_blank', 'noreferrer');
        toast(`Downloading ${res.filename || 'artifact'}…`);
        invalidateEntityData();
        ctx.props.onDone?.();
      } catch (e: any) {
        showErr(`Download failed: ${e?.message || String(e)}`);
      } finally {
        dlBtn.disabled = false;
      }
    });

    // attach / detach this dataset to/from a project (kind === 'dataset')
    // Attach materialises the dataset CSV into the project's files.
    // The server state is reflected after each op by re-reading linked_projects from
    // the datasets list (the /gw/artifacts detail doesn't carry it).
    const projSlot = ctx.el.querySelector<HTMLElement>('.ws-dataset-projects-slot');
    if (a.kind === 'dataset' && projSlot) {
      let linked: LinkedProject[] = [];
      let pickable: { id: string; name: string }[] = [];
      let opBusy = false;

      // Re-read this dataset's linked projects from the datasets list (server truth).
      const refreshLinked = async (): Promise<void> => {
        try {
          const r = await authFetch(`/gw/datasets?q=${encodeURIComponent(a.title || '')}`);
          if (r.ok) {
            const rows = unwrapList<any>(await r.json(), 'datasets');
            const row = rows.find(d => d.id === artifactId);
            linked = Array.isArray(row?.linked_projects) ? row.linked_projects : [];
          }
        } catch { /* keep the last-known linked set */ }
      };

      const renderLinks = () => {
        const linkedIds = new Set(linked.map(l => l.id));
        const options = pickable.filter(p => !linkedIds.has(p.id));
        const chips = linked.length
          ? linked.map(l => `
              <span class="pill neutral" style="display:inline-flex;align-items:center;gap:.3rem">
                <svg class="ico ico-sm"><use href="#i-folder"/></svg>${esc(l.name)}
                <button class="ws-detach-btn" data-detach="${esc(l.id)}" aria-label="Detach from ${esc(l.name)}"
                        title="Detach" ${opBusy ? 'disabled' : ''}
                        style="background:none;border:none;cursor:pointer;color:inherit;padding:0;line-height:1;font-size:1rem">×</button>
              </span>`).join(' ')
          : `<span class="muted" style="font-size:.85rem">Not linked to any project yet.</span>`;

        projSlot.innerHTML = `
          <div style="display:flex;flex-wrap:wrap;gap:.4rem;align-items:center;margin-bottom:.5rem">${chips}</div>
          <div style="display:flex;gap:.4rem;align-items:center;flex-wrap:wrap">
            <select class="ws-project-picker" ${opBusy || options.length === 0 ? 'disabled' : ''}
                    style="flex:1 1 12rem;min-width:10rem">
              ${options.length
                ? `<option value="">Choose a project</option>` +
                  options.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')
                : `<option value="">${pickable.length ? 'linked to all your projects' : 'no projects available'}</option>`}
            </select>
            <button class="btn btn-sm btn-primary" data-act="add-project" ${opBusy || options.length === 0 ? 'disabled' : ''}>
              <svg class="ico ico-sm"><use href="#i-plus"/></svg> Add to project</button>
          </div>
          <div class="ws-project-link-msg"></div>`;

        const linkMsg = projSlot.querySelector<HTMLElement>('.ws-project-link-msg')!;
        const showLinkErr = (text: string) => {
          linkMsg.innerHTML = `<div class="alert-banner" style="background:var(--alert-50);color:var(--alert);margin-top:.4rem">${esc(text)}</div>`;
        };

        // Attach
        projSlot.querySelector<HTMLButtonElement>('button[data-act="add-project"]')
          ?.addEventListener('click', async () => {
            const sel = projSlot.querySelector<HTMLSelectElement>('select.ws-project-picker');
            const pid = sel?.value || '';
            if (!pid) return;
            opBusy = true; renderLinks();
            try {
              const rr = await authFetch(`/gw/datasets/${encodeURIComponent(artifactId)}/projects`, {
                method: 'POST',
                headers: { 'content-type': 'application/json' },
                body: JSON.stringify({ project_id: pid }),
              });
              if (!rr.ok) { opBusy = false; await refreshLinked(); renderLinks(); showLinkErr(`Could not link: ${await errText(rr)}`); return; }
              await refreshLinked();
              opBusy = false; renderLinks();
              toast('Dataset linked to project');
              invalidateEntityData();
              ctx.props.onDone?.();
            } catch (e: any) {
              opBusy = false; renderLinks();
              showLinkErr(`Could not link: ${e?.message || String(e)}`);
            }
          });

        // Detach
        projSlot.querySelectorAll<HTMLButtonElement>('button[data-detach]').forEach(btn => {
          btn.addEventListener('click', async () => {
            const pid = btn.dataset.detach || '';
            if (!pid) return;
            opBusy = true; renderLinks();
            try {
              const rr = await authFetch(
                `/gw/datasets/${encodeURIComponent(artifactId)}/projects/${encodeURIComponent(pid)}`,
                { method: 'DELETE' });
              if (!rr.ok) { opBusy = false; await refreshLinked(); renderLinks(); showLinkErr(`Could not detach: ${await errText(rr)}`); return; }
              await refreshLinked();
              opBusy = false; renderLinks();
              toast('Dataset detached from project');
              invalidateEntityData();
              ctx.props.onDone?.();
            } catch (e: any) {
              opBusy = false; renderLinks();
              showLinkErr(`Could not detach: ${e?.message || String(e)}`);
            }
          });
        });
      };

      // Initial load: my projects (always contributable) + current links.
      void (async () => {
        try { pickable = (await listProjects({ scope: 'mine' })).map(p => ({ id: p.id, name: p.name })); }
        catch { pickable = []; }
        await refreshLinked();
        renderLinks();
      })();
    }
  },
};
export default def;
