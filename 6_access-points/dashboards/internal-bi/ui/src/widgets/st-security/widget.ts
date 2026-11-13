/** st-security: personal access tokens (plus a note on sessions).
 *
 * The gateway exposes the PAT lifecycle: GET /gw/api-tokens (own tokens),
 * POST /gw/api-tokens (issue: returns the full secret once), DELETE
 * /gw/api-tokens/{id} (soft revoke). Active browser/SSO sessions live in
 * Keycloak and have no gateway endpoint, so the widget says so instead of listing them.
 * The full token secret is shown exactly once on issue and never retrievable. */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';

const esc = (s: unknown) =>
  String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));

function relTime(iso: string | null): string {
  if (!iso) return 'never';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return esc(iso);
  const s = (Date.now() - t) / 1000;
  const abs = Math.abs(s);
  const suffix = s >= 0 ? 'ago' : 'from now';
  if (abs < 60) return 'just now';
  if (abs < 3600) return `${Math.floor(abs / 60)}m ${suffix}`;
  if (abs < 86400) return `${Math.floor(abs / 3600)}h ${suffix}`;
  return `${Math.floor(abs / 86400)}d ${suffix}`;
}

function tokenStatus(t: any, theme: any): { label: string; color: string } {
  if (t.revoked_at) return { label: 'Revoked', color: theme.ink[2] };
  const expired = t.is_active === false || (t.expires_at && Date.parse(t.expires_at) < Date.now());
  if (expired) return { label: 'Expired', color: theme.status.watch };
  return { label: 'Active', color: theme.status.good };
}

async function draw(ctx: WidgetCtx, reveal?: { token: string; warning: string }) {
  const { theme } = ctx;
  let tokens: any[] = [];
  let denied = false;
  let loadErr = false;
  try {
    const r = await authFetch('/gw/api-tokens');
    if (r.status === 403 || r.status === 401) denied = true;
    else if (r.ok) { const j = await r.json(); tokens = Array.isArray(j.tokens) ? j.tokens : []; }
    else loadErr = true;                        // 5xx / unexpected: show an error, not "no tokens"
  } catch { loadErr = true; }

  // Self-service password reset (POST /gw/me/reset-password: Keycloak emails a
  // set-password link) + the caller's real active Keycloak sessions.
  let sessions: any[] = [];
  try {
    const rs = await authFetch('/gw/me/sessions');
    if (rs.ok) { const j = await rs.json(); sessions = Array.isArray(j) ? j : []; }
  } catch { /* leave empty: the section still shows the reset action */ }

  const chip = (v: string, accent: string) => `<span style="display:inline-block;padding:.1rem .45rem;border-radius:999px;
    background:${accent}1a;color:${accent};font-size:.72rem;font-weight:600;margin:.1rem .2rem .1rem 0">${esc(v)}</span>`;

  const rowsHtml = tokens.map(t => {
    const st = tokenStatus(t, theme);
    const scope = t.scope && (t.scope.geographies?.length || t.scope.programs?.length)
      ? [...(t.scope.geographies || []), ...(t.scope.programs || [])].map(v => chip(v, theme.status.info)).join('')
      : `<span style="font-size:.78rem;color:${theme.ink[2]}">full owner scope</span>`;
    const canRevoke = !t.revoked_at;
    return `<tr data-tid="${esc(t.id)}">
      <td><strong>${esc(t.name)}</strong>
        <div style="font-family:${theme.fontMono};font-size:.76rem;color:${theme.ink[2]};margin-top:.15rem">nhdw_pat_${esc(t.prefix)}_…</div></td>
      <td>${scope}</td>
      <td class="num">${esc(t.rate_limit_per_min ?? '-')}/min</td>
      <td>${relTime(t.last_used_at ?? null)}</td>
      <td title="${esc(t.expires_at)}">${t.expires_at ? relTime(t.expires_at) : '-'}</td>
      <td><span style="color:${st.color};font-weight:700">${st.label}</span></td>
      <td>${canRevoke ? `<button class="btn btn-sm btn-ghost" data-act="revoke" data-tid="${esc(t.id)}">Revoke</button>` : ''}</td>
    </tr>`;
  }).join('');

  const revealHtml = reveal ? `
    <div style="margin:.4rem 0 .9rem;padding:.7rem .8rem;border:1px solid ${theme.status.good};border-radius:8px;background:${theme.status.good}12">
      <div style="font-size:.8rem;font-weight:700;color:${theme.status.good};margin-bottom:.35rem">New token, copy it now</div>
      <div style="display:flex;gap:.5rem;align-items:center">
        <code style="flex:1;font-family:${theme.fontMono};font-size:.82rem;word-break:break-all;background:${theme.paper};padding:.4rem .5rem;border-radius:6px">${esc(reveal.token)}</code>
        <button class="btn btn-sm btn-primary" data-act="copy">Copy</button>
      </div>
      <div style="font-size:.78rem;color:${theme.ink[2]};margin-top:.35rem">${esc(reveal.warning)}</div>
    </div>` : '';

  const tableHtml = loadErr
    ? `<p style="font-size:.86rem;color:${theme.status.alert};margin:.2rem 0">Couldn't load your access tokens; the service is unreachable. Try again shortly.</p>`
    : denied
    ? `<p style="font-size:.86rem;color:${theme.ink[2]};margin:.2rem 0">You don't have access to issue or view API tokens.</p>`
    : tokens.length
      ? `<div class="scrollable" style="overflow-x:auto"><table class="dash" style="margin:0;width:100%">
           <thead><tr><th>Name</th><th>Scope</th><th>Rate</th><th>Last used</th><th>Expires</th><th>Status</th><th></th></tr></thead>
           <tbody>${rowsHtml}</tbody></table></div>`
      : `<p style="font-size:.86rem;color:${theme.ink[2]};margin:.2rem 0">No personal access tokens yet. Issue one to authenticate notebooks, BI tools, or exporters that can't carry a Keycloak session.</p>`;

  const sessLabel = sessions.length
    ? `${sessions.length} active session${sessions.length === 1 ? '' : 's'}`
    : 'no other active sessions';
  ctx.el.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:.5rem">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:.5rem .7rem;border:1px solid ${theme.line};border-radius:8px">
        <span style="font-size:.84rem;color:${theme.ink[2]}">
          <strong style="color:${theme.ink[1]}">Password &amp; sessions</strong>, ${esc(sessLabel)} (managed by Keycloak).
          A reset link is emailed to your account.
        </span>
        <button class="btn btn-sm btn-ghost" data-act="reset-pw">Reset password</button>
      </div>
      <div data-reset-msg style="font-size:.82rem;min-height:0"></div>
      <div style="display:flex;align-items:center;justify-content:space-between;gap:1rem;margin-top:.2rem">
        <span style="font-size:.84rem;color:${theme.ink[2]}">Personal access tokens:</span>
        ${denied ? '' : '<button class="btn btn-sm btn-primary" data-act="issue-toggle">Issue token</button>'}
      </div>
      <div data-issue-form style="display:none;padding:.6rem .7rem;border:1px solid ${theme.line};border-radius:8px">
        <div style="display:grid;grid-template-columns:2fr 1fr 1fr auto;gap:.6rem;align-items:end">
          <div class="field-group" style="margin:0"><label>Name</label><input data-f="name" type="text" placeholder="e.g. notebook-export" maxlength="120"/></div>
          <div class="field-group" style="margin:0"><label>Expires (days)</label><input data-f="days" type="number" value="90" min="1" max="365"/></div>
          <div class="field-group" style="margin:0"><label>Rate/min</label><input data-f="rate" type="number" value="120" min="1" max="6000"/></div>
          <button class="btn btn-sm btn-primary" data-act="create">Create</button>
        </div>
        <div data-issue-err style="font-size:.78rem;color:${theme.status.alert};margin-top:.35rem;display:none"></div>
      </div>
      ${revealHtml}
      ${tableHtml}
    </div>`;

  // Interactions
  const q = <T extends HTMLElement>(s: string) => ctx.el.querySelector<T>(s);
  q<HTMLButtonElement>('[data-act="reset-pw"]')?.addEventListener('click', async (e) => {
    const btn = e.currentTarget as HTMLButtonElement;
    const msg = q<HTMLElement>('[data-reset-msg]');
    btn.disabled = true; btn.textContent = 'Sending…';
    try {
      const r = await authFetch('/gw/me/reset-password', { method: 'POST' });
      const j = await r.json().catch(() => ({}));
      if (r.ok && j.sent) {
        if (msg) msg.innerHTML = `<span style="color:${theme.status.good}">A set-password link was emailed to ${esc(j.email || 'your account')}.</span>`;
      } else {
        if (msg) msg.innerHTML = `<span style="color:${theme.status.alert}">${esc(j.detail || 'Could not send the reset email. Try again shortly.')}</span>`;
      }
    } catch {
      if (msg) msg.innerHTML = `<span style="color:${theme.status.alert}">Network error sending the reset email.</span>`;
    } finally { btn.disabled = false; btn.textContent = 'Reset password'; }
  });
  q<HTMLButtonElement>('[data-act="issue-toggle"]')?.addEventListener('click', () => {
    const f = q<HTMLElement>('[data-issue-form]'); if (f) f.style.display = f.style.display === 'none' ? 'block' : 'none';
  });
  q<HTMLButtonElement>('[data-act="copy"]')?.addEventListener('click', () => {
    if (reveal) navigator.clipboard?.writeText(reveal.token);
  });
  q<HTMLButtonElement>('[data-act="create"]')?.addEventListener('click', async () => {
    const name = q<HTMLInputElement>('[data-f="name"]')?.value.trim() || '';
    const days = Number(q<HTMLInputElement>('[data-f="days"]')?.value || 90);
    const rate = Number(q<HTMLInputElement>('[data-f="rate"]')?.value || 120);
    const errEl = q<HTMLElement>('[data-issue-err]');
    if (name.length < 2) { if (errEl) { errEl.textContent = 'Name must be at least 2 characters.'; errEl.style.display = 'block'; } return; }
    try {
      const r = await authFetch('/gw/api-tokens', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, expires_in_days: days, rate_limit_per_min: rate }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { if (errEl) { errEl.textContent = j.detail || `Could not issue token (${r.status}).`; errEl.style.display = 'block'; } return; }
      await draw(ctx, { token: j.token, warning: j.warning || 'Save the token now; it cannot be retrieved later.' });
    } catch { if (errEl) { errEl.textContent = 'Network error issuing token.'; errEl.style.display = 'block'; } }
  });
  ctx.el.querySelectorAll<HTMLButtonElement>('[data-act="revoke"]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.tid!;
      if (!window.confirm('Revoke this token? Anything using it will immediately lose access.')) return;
      btn.disabled = true;
      // Re-draw regardless: a failed revoke leaves the token Active;
      // a successful one flips it to Revoked. Swallow to avoid an unhandled rejection.
      try { await authFetch(`/gw/api-tokens/${encodeURIComponent(id)}?reason=user+revocation`, { method: 'DELETE' }); }
      catch { /* surfaced by the unchanged (still-Active) row after re-draw */ }
      finally { await draw(ctx); }
    });
  });
}

const def: WidgetDef = {
  id: 'st-security',
  reactsTo: [],
  render(ctx) { return draw(ctx); },
};
export default def;
