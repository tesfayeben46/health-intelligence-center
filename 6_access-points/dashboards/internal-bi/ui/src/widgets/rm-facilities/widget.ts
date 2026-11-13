/** Facilities by tier per region: bi_facility.mart_facility_capacity
 *  (region by facility_tier counts), stacked bar. */
import type { WidgetDef } from '../../runtime/types';
import { stackedBar } from '../_shared/charts';
import { regionName } from '../_shared/reference';

// Tier display order + colours (post to hospital).
const TIERS = [
  { id: 'HP', label: 'Health post', color: '#cde0b8' },
  { id: 'HC', label: 'Health centre', color: '#8fb96e' },
  { id: 'PHC', label: 'Primary hospital', color: '#e3a33a' },
  { id: 'PH', label: 'General hospital', color: '#c17d1c' },
  { id: 'GH', label: 'Specialised', color: '#9a4b12' },
  { id: 'SH', label: 'Referral', color: '#6b3410' },
];

const def: WidgetDef = {
  id: 'rm-facilities',
  reactsTo: ['geography'],
  async render(ctx) {
    const rows = await ctx.fetchData();
    if (!rows || !rows.length) { ctx.empty('No facility data for the current scope.'); return; }
    const byRegion: Record<string, Record<string, number>> = {};
    for (const r of rows) {
      const reg = regionName(r.region_id);
      (byRegion[reg] ||= {})[String(r.facility_tier)] = Number(r.n_fac ?? 0);
    }
    const categories = Object.keys(byRegion).sort();
    const present = TIERS.filter(t => rows.some(r => String(r.facility_tier) === t.id));
    if (!categories.length || !present.length) { ctx.empty('No facility data for the current scope.'); return; }
    return stackedBar(ctx.el, {
      categories,
      series: present.map(t => ({ name: t.label, color: t.color, data: categories.map(reg => byRegion[reg][t.id] || 0) })),
    }, ctx.theme);
  },
};
export default def;
