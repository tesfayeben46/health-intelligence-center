/**
 * Supply Stress Map (Resource page).
 * A composite stress index, kept transparent: its components show on toggle and are
 * defined on the card. Supply stress index
 * = 0.45*drug_stress + 0.30*facility_gap + 0.25*readiness_gap.
 * Rendered at admin-1 region level. Source: bi_supply + bi_facility (MFR).
 *
 * Data contract, one row per region:
 *   region:str · composite_score:num(0 to 3) · drug_stress:num · facility_gap:num · readiness_gap:num
 * reactsTo: geography.
 */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { choropleth } from '../_shared/charts';
import { loadRegionGeo } from '../_shared/geo';
import { regionName } from '../_shared/reference';

type Metric = 'composite_score' | 'drug_stress' | 'facility_gap' | 'readiness_gap';
const OPTS: { id: Metric; label: string }[] = [
  { id: 'composite_score', label: 'Composite' },
  { id: 'drug_stress', label: 'Drug stress' },
  { id: 'facility_gap', label: 'Facility gap' },
  { id: 'readiness_gap', label: 'Readiness gap' },
];

const def: WidgetDef = {
  id: 'rm-map',
  reactsTo: ['geography'],
  // Map-like: when a scope reflow grows this card, preserve its aspect:
  // the renderer scales the card height with the width growth.
  reflow: 'ratio',
  render(ctx: WidgetCtx) {
    // Region adaptation (symmetric with dz-region-map): the map always
    // loads the national dataset (geography:'all'; OPA still row-filters to the
    // caller's scope). The global region filter drives the map ZOOM + selection
    // instead of hiding the card.
    const focusId = ctx.filters.geography;
    const focusName = focusId && focusId !== 'all' ? regionName(String(focusId)) : null;
    let metric: Metric = 'composite_score';
    let chartCleanup: (() => void) | undefined;
    let rowsByRegion: Record<string, any> = {};

    ctx.el.innerHTML = `
      <p class="widget-lead" style="margin:0 0 .6rem;font-size:.8rem;line-height:1.35;color:var(--ink-3)">Regions shaded by composite supply stress, darkest where supply is tightest. Click a region to inspect its drivers.</p>
      <div class="local-controls"><span class="lc-label">View</span><div class="seg">
        ${OPTS.map((o, i) => `<button data-metric="${o.id}"${i === 0 ? ' class="active"' : ''}>${o.label}</button>`).join('')}
      </div></div>
      <div class="rm-map-mount" style="min-height:340px"></div>
      <div class="region-info"><span class="ri-name"></span><span class="ri-stats">Click a region to inspect.</span></div>`;
    const mount = ctx.el.querySelector('.rm-map-mount') as HTMLElement;
    const infoName = ctx.el.querySelector('.ri-name') as HTMLElement;
    const infoStats = ctx.el.querySelector('.ri-stats') as HTMLElement;
    const updateInfo = (region: string) => {
      infoName.textContent = region;
      const r = rowsByRegion[region];
      infoStats.innerHTML = r
        ? `Composite <b>${Number(r.composite_score ?? 0).toFixed(1)}</b><span class="sep"></span>Drug <b>${Number(r.drug_stress ?? 0).toFixed(1)}</b><span class="sep"></span>Facility <b>${Number(r.facility_gap ?? 0).toFixed(1)}</b><span class="sep"></span>Readiness <b>${Number(r.readiness_gap ?? 0).toFixed(1)}</b>`
        : 'No supply data for this region.';
    };

    const draw = async () => {
      if (chartCleanup) { chartCleanup(); chartCleanup = undefined; }
      mount.innerHTML = '<p class="muted" style="padding:1rem">Loading…</p>';
      try {
        const rows = await ctx.fetchData({ geography: 'all' });
        mount.innerHTML = '';
        if (!rows || !rows.length) { mount.innerHTML = '<div class="widget-empty">No supply stress geography for the selected filters.</div>'; return; }
        rowsByRegion = {}; const valuesByRegion: Record<string, number> = {};
        // Mart rows key regions by 2-letter region_id (AA/AF/…): translate to the
        // SPA display name so the geojson feature join (normRegion) actually matches.
        // Without this every region reads "no data" and the map renders flat.
        for (const r of rows) { const name = regionName(String(r.region)); rowsByRegion[name] = r; valuesByRegion[name] = Number(r[metric] ?? 0); }
        const geo = await loadRegionGeo();
        // supplyStress is ordered high to low; choropleth maps 0 to first, so reverse it: high stress = dark slate.
        const ramp = [...ctx.theme.ramps.supplyStress].reverse();
        const tooltip = (name: string) => {
          const r = rowsByRegion[name];
          if (!r) return `<b>${name}</b><br/>No data`;
          return `<b>${name}</b><br/>Composite ${Number(r.composite_score ?? 0).toFixed(1)}<br/>Drug ${Number(r.drug_stress ?? 0).toFixed(1)}, Facility ${Number(r.facility_gap ?? 0).toFixed(1)}, Readiness ${Number(r.readiness_gap ?? 0).toFixed(1)}`;
        };
        chartCleanup = choropleth(mount, { valuesByRegion, fmt: (v: number) => v.toFixed(1), onSelect: updateInfo, ramp, tooltip, focusRegion: focusName || undefined, selected: focusName || undefined }, ctx.theme, geo);
      } catch {
        // Never leave the "Loading…" placeholder stuck if the map/geo render throws.
        mount.innerHTML = '<div class="widget-empty">Could not load the supply stress map. Please try again.</div>';
      }
    };

    ctx.el.querySelectorAll<HTMLElement>('[data-metric]').forEach(b => {
      b.onclick = () => {
        metric = b.dataset.metric as Metric;
        ctx.el.querySelectorAll<HTMLElement>('[data-metric]').forEach(x => x.classList.toggle('active', x.dataset.metric === metric));
        draw();
      };
    });
    draw();
    return () => { if (chartCleanup) chartCleanup(); };
  },
};
export default def;
