/**
 * Central chart palette for the Internal-BI ECharts widgets.
 *
 * Single source of truth for chart colours, so the warm brown/tan palette
 * is defined in one place instead of scattered hex literals. Mirrors the CSS tokens in
 * styles/base.css (`--brown`, `--tan`, `--ramp-*`, `--map-*`, status).
 *
 * This is a *presentation* module only: it carries no data or logic.
 * biApp.ts imports `T` and uses it for series colors; chart construction,
 * data, and filter wiring are unchanged.
 */
export const T = {
  // core neutrals
  ink:       '#111827',
  ink2:      '#374151',
  muted:     '#667085',
  axis:      '#94a3b8',
  border:    '#e5e7eb',
  grid:      '#e2e6ec',
  card:      '#ffffff',

  // warm accents (match base.css)
  brown:     '#9a6a3a',
  brownDark: '#6f4a2e',
  riskBrown: '#7a4f25',
  bronze:    '#b08246',
  tan:       '#c8a47e',
  tanLight:  '#e8d4bd',
  tanBg:     '#f4eee7',
  sand:      '#faf6f0',
  slate:     '#334155',
  blueGray:  '#8190a5',

  // status (semantic: red reserved for alert)
  good:      '#15865b',
  watch:     '#9a6a3a',
  alert:     '#c73545',

  // warm 5-stop sequential ramp (charcoal to tan)
  ramp:      ['#1f2937', '#6f4a2e', '#9a6a3a', '#c8a47e', '#e8d4bd'],

  // Ethiopia choropleth: warm sand to brown, 7 stops
  map:       ['#faf6f0', '#f0e6d6', '#dec9a6', '#c8a47e', '#a87535', '#7a4f25', '#4a2e15'],

  // facility-tier ramp (blue-gray to ink, kept cool and neutral)
  tier:      ['#e8eef5', '#c8d3e1', '#8798ad', '#4b5e75', '#172033'],

  // equipment status (working / broken / missing)
  eqWork:    '#334155',
  eqBroken:  '#a87535',
  eqMiss:    '#e5e7eb',

  // categorical accent cycle for multi-series charts
  cat:       ['#9a6a3a', '#c8a47e', '#334155', '#8190a5', '#6f4a2e', '#e8d4bd'],
} as const;

export default T;
