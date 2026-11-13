/**
 * Global filter: a chrome widget rendered in the shell's filter-bar slot, not
 * the page grid. It is a self-contained widget folder like the others; only the
 * shell navigation is hardcoded.
 *
 * It renders one chip per `page.global_filters[]` from the spec and honours each
 * filter's per-role `allowed_values` (ops_perm.role_global_filter, intersected
 * with the user's data scope by the gateway):
 *   · null: wildcard: full catalog + "All"
 *   · [] (empty): denied: the filter is hidden
 *   · [single]: pinned, locked chip (e.g. "Programme: HIV" with a lock icon)
 *   · [subset > 1]: dropdown of only those values
 * Writes go through the filter bus (the single state owner); reacting widgets
 * pick the change up via their `reactsTo`.
 */
import type { WidgetDef, WidgetCtx } from '../../../runtime/types';
import type { ResolvedFilter } from '../../../api/spec';
import { REGIONS, PROGRAMMES, DISEASES, PERIOD_OPTS, COMMODITIES, scopeLabels } from '../../../biApp';
import { getWidgetDef } from '../../../runtime/registry';

// Spec filter id to the bus/biApp filter key it drives.
const DIM_KEY: Record<string, string> = {
  region: 'geography', geography: 'geography', programme: 'programme', period: 'period',
  commodity: 'commodity',
};

interface Opt { value: string; label: string; }

// The full catalog for a wildcard filter.
function catalog(filterId: string): Opt[] {
  if (filterId === 'region' || filterId === 'geography') return REGIONS.map((r: { code: string; name: string }) => ({ value: r.code, label: r.name }));
  if (filterId === 'programme') return [
    ...PROGRAMMES.map((p: { code: string; name: string }) => ({ value: p.code, label: p.name })),
    ...DISEASES.map((d: any) => ({ value: d.key, label: d.name })),
  ];
  if (filterId === 'period') return Object.entries(PERIOD_OPTS).map(([k, l]) => ({ value: k, label: String(l) }));
  if (filterId === 'commodity') return COMMODITIES.map((c: { code: string; name: string }) => ({ value: c.code, label: c.name }));
  return [];
}

function labelFor(filterId: string, value: string): string {
  // The geography wildcard's name comes from the profile: the `scopeLabels`
  // biApp resolves from /gw/profile/copy/common.
  if (value === 'all') return filterId === 'region' || filterId === 'geography'
    ? scopeLabels.allGeography : 'All';
  const hit = catalog(filterId).find(o => o.value === value);
  return hit ? hit.label : value;
}

const def: WidgetDef = {
  id: 'chrome/global-filter',
  reactsTo: ['geography', 'programme', 'period', 'commodity'],   // re-render chips on any change

  render(ctx: WidgetCtx) {
    // Display order is set here: the gateway returns filters ordered by id,
    // but the chips read Region · Period · Programme, except on Service, which
    // puts Programme before Period.
    const isService = String(ctx.page.id || '').endsWith('/service');
    const CHIP_ORDER: Record<string, number> = isService
      ? { region: 0, geography: 0, programme: 1, period: 2, commodity: 3 }
      : { region: 0, geography: 0, period: 1, programme: 2, commodity: 3 };
    // Page-specific filter set: only offer a filter whose dimension a widget on
    // this page reacts to. The resolved spec's reacts_to is null for core
    // widgets, so it is derived from the registry.
    const usedDims = new Set<string>();
    for (const w of (ctx.page.widgets || [])) {
      const wdef = getWidgetDef(w.id);
      for (const d of (wdef?.reactsTo || [])) usedDims.add(d);
    }
    const filters = [...(ctx.page.global_filters || [])]
      .filter(f => usedDims.has(DIM_KEY[f.id] || f.id))
      .sort((a, b) => (CHIP_ORDER[a.id] ?? 99) - (CHIP_ORDER[b.id] ?? 99));
    const el = ctx.el;
    let openId: string | null = null;

    const draw = () => {
      const cur = ctx.bus.get();
      const chips = filters.map((f: ResolvedFilter) => {
        const key = DIM_KEY[f.id] || f.id;
        const av = f.allowed_values;                  // null = wildcard, [] = denied
        if (Array.isArray(av) && av.length === 0) return '';        // denied: hidden
        const value = cur[key] ?? 'all';
        const locked = Array.isArray(av) && av.length === 1;
        const scoped = Array.isArray(av) && av.length >= 1;
        // Option set: scoped: exactly allowed_values; wildcard to All + catalog.
        const opts: Opt[] = scoped
          ? av!.map(v => ({ value: v, label: labelFor(f.id, v) }))
          : [{ value: 'all', label: labelFor(f.id, 'all') }, ...catalog(f.id)];
        const shownVal = locked ? av![0] : value;
        const active = shownVal !== 'all';

        if (locked) {
          return `<span class="dropdown">
            <span class="filter-chip locked" title="Locked to your role's scope">
              <span class="key">${f.title || f.id}</span>
              <span class="val">${labelFor(f.id, av![0])}</span>
              <svg class="ico ico-sm"><use href="#i-lock"/></svg>
            </span></span>`;
        }
        const menu = openId === f.id ? `
          <div class="dropdown-menu" data-menu="${f.id}">
            ${scoped ? `<div class="dd-section">${f.title || f.id} (scoped)</div>` : ''}
            ${opts.map(o => `<div class="dd-item${o.value === shownVal ? ' selected' : ''}" data-key="${key}" data-val="${o.value}">${o.label}</div>`).join('')}
          </div>` : '';
        return `<span class="dropdown">
          <span class="filter-chip${active ? ' primary' : ''}${openId === f.id ? ' active-open' : ''}" data-open="${f.id}">
            <span class="key">${f.title || f.id}</span>
            <span class="val">${labelFor(f.id, shownVal)}</span>
            <svg class="ico ico-sm"><use href="#i-chevron-down"/></svg>
          </span>${menu}</span>`;
      }).join('');

      // No visible filters for this page (none used, or all denied): render
      // nothing and collapse the slot rather than show an empty filter bar.
      if (!chips.trim()) { el.innerHTML = ''; return; }

      el.innerHTML = `
        <div class="gf-panel"><div class="gf-row">
          <div class="gf-icon"><svg><use href="#i-filter"/></svg></div>
          <div class="gf-chips">${chips}</div>
          <div class="gf-actions">
            <button class="gf-reset" data-reset><svg class="ico ico-sm"><use href="#i-refresh"/></svg> Reset</button>
          </div>
        </div></div>`;

      // Wire interactions.
      el.querySelectorAll<HTMLElement>('[data-open]').forEach(chip => {
        chip.onclick = (e) => { e.stopPropagation(); openId = openId === chip.dataset.open ? null : chip.dataset.open!; draw(); };
      });
      el.querySelectorAll<HTMLElement>('.dd-item').forEach(item => {
        item.onclick = (e) => { e.stopPropagation(); openId = null; ctx.bus.set(item.dataset.key!, item.dataset.val!); };
      });
      const reset = el.querySelector<HTMLButtonElement>('[data-reset]');
      if (reset) reset.onclick = (e) => {
        e.stopPropagation();
        openId = null;
        // Only reset dimensions the role may widen (skip locked ones).
        for (const f of filters) {
          const av = f.allowed_values;
          if (Array.isArray(av) && av.length <= 1) continue;   // denied or locked
          ctx.bus.set(DIM_KEY[f.id] || f.id, 'all');
        }
        draw();
      };
    };

    const onDoc = () => { if (openId) { openId = null; draw(); } };
    document.addEventListener('click', onDoc);
    draw();
    return () => document.removeEventListener('click', onDoc);
  },
};
export default def;
