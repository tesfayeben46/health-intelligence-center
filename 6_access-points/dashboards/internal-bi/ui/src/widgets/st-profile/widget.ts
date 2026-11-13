/** st-profile: the signed-in user's identity, role and data scope.
 *
 * Sourced from the identity provider via the gateway: GET /gw/me (name/email/
 * geographies/programs/locale) + GET /gw/permissions/me (authoritative DB role
 * codes + capabilities). There is no profile-edit endpoint (Keycloak owns the
 * identity), so the widget is read-only. */
import type { WidgetDef } from '../../runtime/types';
import { authFetch } from '../../api/useAuth';
import { scopeLabels } from '../../biApp';

const esc = (s: unknown) =>
  String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));

const chips = (vals: string[], accent: string, emptyLabel: string) =>
  vals && vals.length
    ? vals.map(v => `<span style="display:inline-block;padding:.12rem .5rem;border-radius:999px;
        background:${accent}1a;color:${accent};font-size:.76rem;font-weight:600;margin:.12rem .25rem .12rem 0">${esc(v)}</span>`).join('')
    : `<span style="font-size:.82rem;color:#9a9384">${esc(emptyLabel)}</span>`;

const def: WidgetDef = {
  id: 'st-profile',
  reactsTo: [],
  async render(ctx) {
    const { theme } = ctx;
    let me: any, perm: any;
    try {
      const [rMe, rPerm] = await Promise.all([authFetch('/gw/me'), authFetch('/gw/permissions/me')]);
      if (!rMe.ok) { ctx.empty('Could not load your profile.'); return; }
      me = await rMe.json();
      perm = rPerm.ok ? await rPerm.json() : {};
    } catch { ctx.empty('Could not load your profile.'); return; }

    const field = (label: string, value: string) => `
      <div style="display:flex;flex-direction:column;gap:.2rem">
        <span style="font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;color:${theme.ink[2]}">${esc(label)}</span>
        <span style="font-size:.95rem;color:${theme.ink[0]};font-weight:500">${esc(value) || '-'}</span>
      </div>`;

    const roleCodes: string[] = perm.roles?.length ? perm.roles : (me.roles || []);
    const geos: string[] = me.geographies || [];
    const progs: string[] = me.programs || [];
    const caps: string[] = perm.capabilities || [];
    const geoLabel = geos.length ? '' : `${scopeLabels.allGeography} (national)`;
    const progLabel = progs.length ? '' : 'All programmes';

    ctx.el.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:1.0rem 1.4rem;max-width:760px">
        ${field('Full name', me.full_name)}
        ${field('Username', me.username)}
        ${field('Email', me.email)}
        ${field('Primary role', me.primary_role || perm.primary_role || '-')}
        <div style="display:flex;flex-direction:column;gap:.25rem">
          <span style="font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;color:${theme.ink[2]}">Roles</span>
          <div>${chips(roleCodes, theme.navy['700'] || '#1d3557', 'none')}</div>
        </div>
        ${field('Locale', me.locale || 'en')}
        <div style="display:flex;flex-direction:column;gap:.25rem">
          <span style="font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;color:${theme.ink[2]}">Geography scope</span>
          <div>${geos.length ? chips(geos, theme.status.info, '') : `<span style="font-size:.9rem;color:${theme.ink[0]}">${geoLabel}</span>`}</div>
        </div>
        <div style="display:flex;flex-direction:column;gap:.25rem">
          <span style="font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;color:${theme.ink[2]}">Programme scope</span>
          <div>${progs.length ? chips(progs, theme.status.good, '') : `<span style="font-size:.9rem;color:${theme.ink[0]}">${progLabel}</span>`}</div>
        </div>
        <div style="grid-column:span 2;display:flex;flex-direction:column;gap:.25rem">
          <span style="font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;color:${theme.ink[2]}">Capabilities</span>
          <div>${chips(caps, theme.ink[2], 'none')}</div>
        </div>
        <p style="grid-column:span 2;margin:.2rem 0 0;font-size:.8rem;color:${theme.ink[2]};line-height:1.5">
          Your identity, role and data scope are managed centrally by the national
          identity provider (Keycloak) and enforced on every request. To change
          your role or scope, contact a data-governance administrator.
        </p>
      </div>`;
  },
};
export default def;
