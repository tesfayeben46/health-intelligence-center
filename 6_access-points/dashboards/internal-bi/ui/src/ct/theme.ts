/**
 * ECharts palette for the Supply Chain Control Tower: mirrors the cool-slate
 * design tokens in styles/base.css (the shared NHDW design language). The map
 * routes use the info-blue family.
 */
export const T = {
  ink:        '#0F172A',
  ink2:       '#334155',
  muted:      '#64748B',
  faint:      '#94A3B8',
  border:     '#E5E9F0',
  border2:    '#D6DDE8',
  paper:      '#FFFFFF',
  land:       '#EEF2F7',
  landBorder: '#D6DDE8',

  // route / node blues
  route:      '#3B82F6',
  routeSoft:  'rgba(59,130,246,0.28)',
  routeFaint: 'rgba(59,130,246,0.14)',
  node:       '#2563EB',
  hub:        '#0F172A',

  // status
  good:  '#22A06B',
  watch: '#F59E0B',
  alert: '#EF4444',
  info:  '#3B82F6',

  // sankey / flow categorical
  cat: ['#334155', '#3B82F6', '#22A06B', '#64748B', '#0EA5E9', '#8B5CF6', '#F59E0B'],
} as const;

export default T;
