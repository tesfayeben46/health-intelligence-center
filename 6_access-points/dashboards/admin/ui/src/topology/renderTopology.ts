/**
 * renderTopology: a framework-agnostic imperative renderer for the NHDW
 * system-topology graph (applications only).
 *
 * Layout: three stacked bands, top to bottom.
 *     ACCESS · ENDPOINTS                    the SPAs
 *     APIS · GATEWAY                        the API/data gateway the SPAs call through
 *     STORAGE · COMPUTE · SECURITY · OPS    everything behind it, side by side
 * Zones auto-size to their node count; the gap between nodes is at least half a
 * card width; nodes never overlap each other or their zone, and zones never
 * overlap. This holds as spawned pods appear and disappear.
 *
 * Cards: white rounded rects on an off-white grid canvas, icon above a title
 * (details live in the click-through panel), with a health pip top-right. Each
 * card shows the tool's brand mark in its brand colours (BRAND_ICONS); NHDW's own
 * apps use a neutral-slate line glyph and unknown nodes fall back to iconFor().
 * Phantom (declared, not deployed) cards are flat, grey and dashed with a
 * desaturated icon; external, ephemeral (e.g. JupyterHub single-user labs) and
 * control nodes get a dashed rim. Each zone box border uses its ZONE_HUES colour.
 *
 * Edges: orthogonal, routed by A* over a Hanan grid (lines through every port and
 * every obstacle edge) with a turn penalty. For each edge, every pair of the two
 * cards' four side-midpoint ports is costed and the cheapest wins, so a line
 * enters its target at the nearest clean port. The only hard obstacles are the
 * zone title text rects; zone boxes and cards are pass-through (edges paint
 * beneath the cards), though running under a card costs more. A post-pass removes
 * bends no obstacle requires, and edges sharing a card side fan out into lanes.
 * Corners are rounded; traffic edges animate as marching ants (speed scales with req/s).
 *
 * Cytoscape (core only) draws and hit-tests the cards; zones, shadows and edges
 * are painted on a synced canvas overlay beneath them. No external fetch: icons
 * are embedded SVG (offline, CSP-locked). Chrome colours come from the `theme`
 * tokens the caller resolves via getComputedStyle; the zone hues and brand
 * colours are fixed and not theme tokens.
 */
import cytoscape from 'cytoscape';

// data contract (this module owns it; api/topology.ts conforms)
export type TopoHealth = 'up' | 'degraded' | 'down' | 'unknown';

export interface TopoNode {
  id: string;
  label: string;
  subtitle?: string | null;   // one-line "what is this": shown in the DETAIL panel
  tier: string;
  kind: string;
  zone?: string | null;       // placement-zone id assigned by the gateway (apis/
                              // access/storage/compute/security/observability);
                              // the renderer owns the zone GEOMETRY, the backend
                              // owns the semantic zone, so they stay in lockstep.
  declared: boolean;
  observed: boolean;
  ephemeral?: boolean;        // dynamically-spawned pod (e.g. a JupyterHub lab):
                              // observed + running but not a manifest workload.
  health: TopoHealth;
  phantom: boolean;
  k8s: { deployment: string; ready: string; namespace: string; pod?: string } | null;
}

export interface TopoTraffic {
  reqRate: number;
  errRate: number;
  p95Ms: number | null;
  bytes: number;
  window: string;
  samples: number;
  source: string;
  series?: number[] | null;   // per-bucket req/s over the window (real sparkline)
}

export interface TopoEdge {
  id: string;
  source: string;
  target: string;
  kind: string;
  declared: boolean;
  observed: boolean;
  status: string;            // up | degraded | down | unknown
  traffic?: TopoTraffic | null;
}

export interface TopoGraph {
  nodes: TopoNode[];
  edges: TopoEdge[];
  as_of: string;
  meta?: { traffic_sources?: string[]; window?: string };
}

/** Design-token strings the host resolves (getComputedStyle to base.css). */
export interface TopoTheme {
  paper: string; paper2: string; bg: string; bg2: string;
  border: string; border2: string;
  ink: string; ink2: string; ink3: string; ink4: string;
  good: string; watch: string; alert: string; info: string;
  navy700: string; navy50: string;
  fontSans: string; fontMono: string;
}

export interface TopoHandlers {
  onNodeSelect?(node: TopoNode): void;
  onEdgeSelect?(edge: TopoEdge): void;
  onBackgroundTap?(): void;
}

/** Callable cleanup fn with imperative controls attached (portable handle). */
export interface TopologyHandle {
  (): void;                       // cleanup: destroy the instance
  update(graph: TopoGraph): void; // in-place refresh, viewport preserved
  zoomIn(): void;
  zoomOut(): void;
  fit(): void;
  resetLayout(): void;
  select(id: string | null): void;
}

// card + grid geometry (graph units)
const NODE_W = 190;            // card width
const NODE_H = 139;            // card height
const HW = NODE_W / 2;
const HH = NODE_H / 2;
// Transparent margin around the card SVG, with the card rect inset by it so the
// rect still maps onto the cytoscape node box (NODE_W by NODE_H). Zero: the card SVG
// has no baked shadow; the drop shadow is drawn on the overlay canvas beneath.
const SHADOW_PAD = 0;
const CARD_SVG_W = NODE_W + 2 * SHADOW_PAD;
const CARD_SVG_H = NODE_H + 2 * SHADOW_PAD;
const COL_GAP = 96;            // at least half a card width
const ROW_GAP = 84;            // vertical breathing room between card rows
const ZONE_PAD_X = 45;         // node area inset inside a zone rectangle (sides)
const ZONE_PAD_TOP = 65;       // top inset, leaves room for the zone title
const ZONE_PAD_BOT = 45;       // bottom inset
const TITLE_CHAR_PX = 6.4;     // ~ per-glyph advance in SCREEN px of the drawn title font.
                               // The title is ANCHORED in graph space but DRAWN in screen
                               // space (fixed +14,+20 px offsets, zoom-clamped font), so its
                               // graph-unit footprint grows as the view zooms out. The
                               // obstacle rect therefore scales these screen metrics by the
                               // estimated fit zoom (see zoneTitleRect), so lines clear the
                               // text at the zoom actually shown, not just at z=1.
const BAND_GAP = 66;           // vertical gap between the stacked bands
const ZONE_GAP = 58;           // horizontal gap between adjacent backend zones
const MARGIN = 36;             // diagram margin (bounds padding)

// Router (Hanan-grid A*, port-pair selection, title-text-only obstacles)
const TURN_PENALTY = 90;       // A* cost per 90° turn; high, so straight runs are preferred
const PORT_STUB = 18;          // clean perpendicular stub leaving/entering a port
const TEXT_OBST_MARGIN = 8;    // expansion around a title-text rect (the only hard obstacle)
const LANE_GAP = 16;           // spread between multiple edges sharing one card side
const PORT_INSET = 16;         // keep fanned ports this far in from a card's corners
const CARD_CROSS_FACTOR = 5;   // soft cost multiplier for running under a card. Cards are
                               // pass-through, but a route tunnelling beneath a row of cards
                               // reads badly, so card-interior distance costs 5x: the router
                               // goes around and enters at the nearest clean port unless the
                               // detour is much longer.

type Side = 'R' | 'L' | 'U' | 'D';

// embedded line-art icons (24x24, stroke=currentColor)
// A self-contained subset of the app icon set (static/assets/icons.html) so the
// renderer needs no sprite fetch and can colour each icon per node. Inner SVG
// only: nodeBgUri() wraps it with the chosen stroke colour.
const ICONS: Record<string, string> = {
  link: `<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 1 0-7-7l-1 1M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 1 0 7 7l1-1"/>`,
  lock: `<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>`,
  shield: `<path d="M12 2 4 5v6c0 5 3.5 8.5 8 11 4.5-2.5 8-6 8-11V5l-8-3Z"/>`,
  'chart-bar': `<path d="M3 3v18h18M7 16v-5M12 16V8M17 16v-3"/>`,
  'chart-line': `<path d="M3 3v18h18M7 14l3-3 3 3 5-5"/>`,
  globe: `<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20"/>`,
  notebook: `<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2zM22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>`,
  book: `<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/>`,
  database: `<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v6c0 1.7 4 3 9 3s9-1.3 9-3V5M3 11v6c0 1.7 4 3 9 3s9-1.3 9-3v-6"/>`,
  package: `<path d="m16.5 9.4-9-5.2M21 16V8a2 2 0 0 0-1-1.7l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.7l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5M12 22V12"/>`,
  cpu: `<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/><rect x="9" y="9" width="6" height="6"/>`,
  refresh: `<path d="M3 12a9 9 0 0 1 15-6.7L21 8M21 3v5h-5M21 12a9 9 0 0 1-15 6.7L3 16M3 21v-5h5"/>`,
  microscope: `<path d="M6 18h8M3 22h18M14 22a7 7 0 1 0 0-14M9 14h2M9 12a2 2 0 0 1-2-2V6h4v4a2 2 0 0 1-2 2ZM12 6h.01M11 4 9 6V4Z"/>`,
  chat: `<path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z"/>`,
  key: `<circle cx="7.5" cy="15.5" r="4.5"/><path d="m10.7 12.3 9.3-9.3M15 5l4 4M14 10l3 3"/>`,
  policy: `<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z"/><path d="M14 3v6h6M8 13h8M8 17h6"/>`,
  user: `<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>`,
  eye: `<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12Z"/><circle cx="12" cy="12" r="3"/>`,
  settings: `<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.6 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.6-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/>`,
  pulse: `<path d="M3 12h4l2-6 4 12 2-6h6"/>`,
  list: `<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>`,
  bell: `<path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0"/>`,
  server: `<rect x="2" y="3" width="20" height="8" rx="1"/><rect x="2" y="13" width="20" height="8" rx="1"/><path d="M6 7h.01M6 17h.01"/>`,
  grid: `<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>`,
};

// branded tool icons (24x24, coloured in each tool's brand hue)
// Simplified brand marks as inline SVG paths only: the page is offline and
// CSP-locked (no external URLs, no <img>, no remote fonts). Keyed by node id and
// checked first; unknown nodes fall back to the mono iconFor() glyph. NHDW's own
// apps have no external brand and use a neutral-slate line glyph, which keeps
// them visually distinct from the third-party tools.
const SLATE_ICON = '#334155';   // neutral slate for our own NHDW apps
const slate = (inner: string): string =>
  `<g fill='none' stroke='${SLATE_ICON}' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'>${inner}</g>`;
const ID_CARD =
  `<rect x='3' y='5' width='18' height='14' rx='2'/><circle cx='8.5' cy='11' r='2.2'/>` +
  `<path d='M5 16.4c.5-1.8 1.9-2.7 3.5-2.7s3 .9 3.5 2.7'/><path d='M13.5 9.6h4.5M13.5 13h4.5M13.5 16.2h3'/>`;

const BRAND_ICONS: Record<string, string> = {
  // external branded tools: self-coloured brand marks
  'postgres-vault':                                                   // PostgreSQL blue: database/elephant motif
    `<ellipse cx='12' cy='6.2' rx='7' ry='2.7' fill='#336791'/>` +
    `<path d='M5 6.2v11.6c0 1.5 3.1 2.7 7 2.7s7-1.2 7-2.7V6.2c0 1.5-3.1 2.7-7 2.7s-7-1.2-7-2.7z' fill='#336791'/>` +
    `<ellipse cx='12' cy='6.2' rx='7' ry='2.7' fill='#5b89ad'/>` +
    `<path d='M5 11.8c0 1.5 3.1 2.7 7 2.7s7-1.2 7-2.7M5 15c0 1.5 3.1 2.7 7 2.7s7-1.2 7-2.7' fill='none' stroke='#e8f0f6' stroke-width='.9' opacity='.7'/>`,
  'clickhouse':                                                       // ClickHouse: signature yellow bars on dark
    `<rect x='2.5' y='2.5' width='19' height='19' rx='3' fill='#1c1c1c'/>` +
    `<g fill='#FAFF69'>` +
      `<rect x='5.4' y='6' width='2.5' height='12' rx='.4'/>` +
      `<rect x='9.2' y='6' width='2.5' height='12' rx='.4'/>` +
      `<rect x='13' y='6' width='2.5' height='12' rx='.4'/>` +
      `<rect x='16.8' y='10.9' width='2.5' height='2.4' rx='.4'/>` +
    `</g>`,
  'seaweedfs-filer':                                                  // SeaweedFS: stacked volumes, teal
    `<ellipse cx='12' cy='7' rx='7' ry='2.6' fill='#1FA2A6'/><path d='M5 7v4c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6V7' fill='none' stroke='#1FA2A6' stroke-width='1.6'/><path d='M5 11v4c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6v-4' fill='none' stroke='#1FA2A6' stroke-width='1.6'/>`,
  'nhdw-trino':                                                       // Trino magenta: rabbit
    `<path d='M8.4 10c-.5-2-.8-3.9-.6-5.9.1-.8 1.1-1 1.5-.3.9 1.4 1.5 3 1.8 4.7h1.8c.3-1.7.9-3.3 1.8-4.7.4-.7 1.4-.5 1.5.3.2 2-.1 3.9-.6 5.9 1.3.9 2.1 2.4 2.1 4 0 2.9-2.5 4.7-5.7 4.7s-5.7-1.8-5.7-4.7c0-1.6.8-3.1 2.1-4z' fill='#DD00A1'/>` +
    `<circle cx='10.2' cy='14.2' r='.9' fill='#fff'/><circle cx='13.8' cy='14.2' r='.9' fill='#fff'/>`,
  'airflow':                                                          // Airflow blue: pinwheel
    `<g fill='#017CEE'>` +
      `<path d='M12 12 5.7 4.7c-2 1.7-2.6 4.6-1.4 7L12 12z'/>` +
      `<path d='M12 12 20.9 10c-.5-2.6-2.6-4.5-5.3-4.9L12 12z'/>` +
      `<path d='M12 12 13.9 20.8c2.6-.4 4.6-2.5 5-5.1L12 12z'/>` +
    `</g><circle cx='12' cy='12' r='1.7' fill='#017CEE'/>`,
  'mlflow':                                                           // MLflow blue: ink-splash mark
    `<path d='M6 13.2c-1.6-3 .3-6.6 3.8-7 1.4-.2 2.3.6 3.7.4 2.6-.4 5 1.3 5.4 3.7.4 2.4-1.1 4.5-3.4 5.2.3 1.2-.7 2.4-2 2.2-.9-.1-1.5-.9-1.6-1.7-2.7.3-5.1-.9-5.9-2.8z' fill='#0194E2'/>` +
    `<circle cx='10' cy='11.4' r='1.4' fill='#fff'/>`,
  'nhdw-ollama':                                                      // Ollama near-black: llama
    `<path d='M8 4.2c.5 0 .9.4.9.9v1.7c.4-.2.9-.3 1.4-.3h3.4c.5 0 1 .1 1.4.3V5.1c0-.5.4-.9.9-.9s.9.4.9.9v2.8c.6.7 1 1.6 1 2.7v5.6c0 1.6-1.3 2.9-2.9 2.9v.9c0 .5-.4.8-.9.8s-.9-.3-.9-.8v-.9h-1.6v.9c0 .5-.4.8-.9.8s-.9-.3-.9-.8v-.9c-1.6 0-2.9-1.3-2.9-2.9v-5.6c0-1.1.4-2 1-2.7V5.1c0-.5.4-.9.9-.9z' fill='#101010'/>` +
    `<circle cx='10' cy='11' r='.85' fill='#fff'/><circle cx='14' cy='11' r='.85' fill='#fff'/>`,
  'jupyterhub':                                                       // Jupyter orange: three circles
    `<g fill='#F37726'>` +
      `<circle cx='12' cy='5.4' r='2.1'/><circle cx='6.4' cy='16' r='2.4'/><circle cx='17.6' cy='16' r='2.4'/>` +
    `</g>`,
  'keycloak':                                                         // Keycloak teal: keyhole
    `<circle cx='12' cy='12' r='9' fill='#008AAA'/>` +
    `<circle cx='12' cy='10' r='2.7' fill='#fff'/><path d='M10.3 11.4h3.4l-.9 5.4h-1.6z' fill='#fff'/>`,
  'opa':                                                              // OPA slate-blue: policy shield + check
    `<path d='M12 3 5 5.6v5.2c0 4.3 3 7.6 7 9.2 4-1.6 7-4.9 7-9.2V5.6L12 3z' fill='#566B78'/>` +
    `<path d='M8.6 12.1l2.4 2.4 4.4-4.7' fill='none' stroke='#fff' stroke-width='1.9' stroke-linecap='round' stroke-linejoin='round'/>`,
  'kubernetes-api':                                                   // Kubernetes blue: heptagon + helm
    `<path d='M12 3 19 6.4 20.8 14 15.9 20.1H8.1L3.2 14 5 6.4z' fill='#326CE5'/>` +
    `<g fill='none' stroke='#fff' stroke-width='1.3' stroke-linecap='round'>` +
      `<circle cx='12' cy='12' r='3.2'/>` +
      `<path d='M12 4.4v3.1M12 20v-2.9M5.2 8.6l2.9 1.4M18.8 15.4l-3-1.4M5.2 15.4l2.9-1.4M18.8 8.6l-3 1.4'/>` +
    `</g>`,
  'prometheus':                                                       // Prometheus orange: flame
    `<path d='M12 2.6c1.7 2.6 1.1 4.6.1 6.2-.8 1.4-1.4 2.7-.7 4.3.4-1.2 1.1-2 2-2.7.4 1.9.7 3-.1 4.5 1.6-.9 2.6-2.5 2.7-4.5.4 1 .7 2 .7 3.1 0 3.7-3 6.6-7 6.6s-7-2.9-7-6.6c0-2.3 1.2-4.3 2.7-6-.2 1.2.1 2.3.9 3.2-.4-3.1 1.3-5.8 3.7-8.1z' fill='#E6522C'/>`,
  'grafana':                                                          // Grafana orange: orb
    `<circle cx='12' cy='12' r='8.6' fill='#F46800'/>` +
    `<path d='M8 13.6c.3-3.1 2.5-5.2 5.6-5.4-2.1 1.2-3.3 3.1-3.8 5.6-.2 1.1-1.6 1-1.8-.2z' fill='#fff' opacity='.92'/>`,
  'loki':                                                             // Loki/Grafana orange: log lines
    `<g fill='#F5A623'>` +
      `<rect x='4' y='6' width='16' height='2.7' rx='1.35'/>` +
      `<rect x='4' y='10.7' width='11.5' height='2.7' rx='1.35'/>` +
      `<rect x='4' y='15.4' width='14' height='2.7' rx='1.35'/>` +
    `</g>`,
  'promtail':                                                         // Promtail: same orange family, ship/tail
    `<path d='M3 12 20.5 4.5 13.7 20.8l-3-6.3z' fill='#F5A623'/>` +
    `<path d='M11 14 20.5 4.5' fill='none' stroke='#fff' stroke-width='1.1' opacity='.7'/>`,
  'alertmanager':                                                     // Alertmanager (Prometheus orange): bell
    `<path d='M12 3a1.3 1.3 0 0 1 1.3 1.3v.6a5.6 5.6 0 0 1 4.3 5.4v3l1.4 2.3c.4.7-.1 1.6-.9 1.6H6c-.9 0-1.4-.9-.9-1.6L6.4 16.3v-3a5.6 5.6 0 0 1 4.3-5.4v-.6A1.3 1.3 0 0 1 12 3z' fill='#E6522C'/>` +
    `<path d='M10 19.2a2 2 0 0 0 4 0' fill='none' stroke='#E6522C' stroke-width='1.7' stroke-linecap='round'/>`,
  'dbt-docs':                                                         // dbt orange: circle + hash
    `<circle cx='12' cy='12' r='9' fill='#FF694B'/>` +
    `<g stroke='#fff' stroke-width='1.7' stroke-linecap='round'>` +
      `<path d='M10.2 7.4 8.7 16.6M15.3 7.4 13.8 16.6M7.4 10.4h9.2M6.9 13.9h9.2'/>` +
    `</g>`,
  'headlamp':                                                         // Headlamp: teal lamp/beam
    `<path d='M5.5 9.2a3.8 3.8 0 0 1 3.8-3.8H11v13.2H9.3a3.8 3.8 0 0 1-3.8-3.8z' fill='#14B8A6'/>` +
    `<path d='M11 6.6 19 3.4v17.2L11 17.4z' fill='#14B8A6' opacity='.85'/>` +
    `<path d='M20 9h2.5M19.5 5.6 22 4.2M19.5 14.4 22 15.8' fill='none' stroke='#14B8A6' stroke-width='1.4' stroke-linecap='round'/>`,
  // NHDW's own apps: neutral-slate line glyphs
  'admin': slate(ICONS.shield),
  'internal-bi': slate(ICONS['chart-bar']),
  'public-dashboard': slate(ICONS.globe),
  'workshop': slate(ICONS.notebook),
  'nhdw-gateway': slate(ICONS.link),
  'fayda-replica': slate(ID_CARD),
};

// which icon best represents a node: by id first, then kind, then tier.
const ICON_BY_ID: Record<string, string> = {
  'nhdw-gateway': 'link',
  'admin': 'shield', 'internal-bi': 'chart-bar', 'public-dashboard': 'globe',
  'workshop': 'notebook', 'dbt-docs': 'book',
  'postgres-vault': 'database', 'seaweedfs-filer': 'package', 'clickhouse': 'database',
  'nhdw-trino': 'cpu', 'airflow': 'refresh', 'mlflow': 'microscope', 'nhdw-ollama': 'chat',
  'jupyterhub': 'notebook',
  'keycloak': 'key', 'opa': 'policy', 'fayda-replica': 'user',
  'headlamp': 'eye', 'kubernetes-api': 'settings',
  'prometheus': 'pulse', 'loki': 'list', 'alertmanager': 'bell',
  'promtail': 'list', 'grafana': 'chart-line',
};
const ICON_BY_KIND: Record<string, string> = {
  ui: 'chart-bar', datastore: 'database', security: 'shield', ml: 'microscope',
  engine: 'cpu', orchestrator: 'refresh', platform: 'settings', ops: 'pulse',
  external: 'globe', api: 'link', notebook: 'notebook',
};
const ICON_BY_TIER: Record<string, string> = {
  dashboard: 'chart-bar', api: 'link', storage: 'database', compute: 'cpu',
  security: 'shield', external: 'globe', ops: 'pulse', observability: 'pulse',
};
function iconFor(n: TopoNode): string {
  if (n.ephemeral) return 'notebook';
  return ICON_BY_ID[n.id] || ICON_BY_KIND[n.kind] || ICON_BY_TIER[n.tier] || 'server';
}

// colour helpers (derive rgba from a token hex: no hardcoded palette)
function hexToRgba(hex: string, alpha: number): string {
  let h = (hex || '').trim().replace('#', '');
  if (h.length === 3) h = h.split('').map(c => c + c).join('');
  if (h.length !== 6) return `rgba(127,127,127,${alpha})`;
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

/** the health-dot colour for a card (top-right pip). */
function healthColor(n: TopoNode, theme: TopoTheme): string {
  if (n.phantom) return theme.ink4;
  if (n.kind === 'external' || n.tier === 'external') return theme.info;
  switch (n.health) {
    case 'up':       return '#22c55e';   // bright liveness green
    case 'degraded': return theme.watch;
    case 'down':     return theme.alert;
    default:         return theme.ink4;
  }
}

/** Per-zone hues, one per placement box, used only for the zone box border
 *  (drawn in paint()). The family spans the wheel (clay, gold, sage, teal,
 *  steel-blue, plum) at consistent muted saturation and mid lightness, so zones
 *  are distinct but cohesive. */
const ZONE_HUES: Record<ZoneId, { name: string; base: string }> = {
  access:        { name: 'Clay',       base: '#B0573A' },
  apis:          { name: 'Ochre Gold', base: '#BC913F' },
  storage:       { name: 'Sage',       base: '#7E9455' },
  compute:       { name: 'Teal',       base: '#3F8A83' },
  security:      { name: 'Steel Blue', base: '#5E7BA0' },
  observability: { name: 'Plum',       base: '#8A6796' },
};
const CARD_BORDER = '#d3dae5';   // cool-gray 1px rim (phantom cards)

/** The node card as one self-contained SVG data URI: a white rounded face, the
 *  tool's brand icon (BRAND_ICONS, else a neutral-slate glyph for NHDW apps or
 *  the mono fallback for unknown nodes) and the health pip top-right. The rect
 *  is exactly the node box, so the cytoscape state rings (degraded, down,
 *  selected) land on the card edge. Phantom (not-deployed) cards get a grey face,
 *  a dashed rim and a desaturated icon; external, ephemeral and synthesized
 *  control nodes get a dashed rim. */
function nodeBgUri(n: TopoNode, theme: TopoTheme): string {
  const iconInner = BRAND_ICONS[n.id] ?? slate(ICONS[iconFor(n)] || ICONS.server);
  const dot = healthColor(n, theme);
  const isExt = n.kind === 'external' || n.tier === 'external';
  const dashed = !!(n.ephemeral || isExt || n.id === 'kubernetes-api');
  const dash = (dashed || n.phantom) ? " stroke-dasharray='5 4'" : '';

  const ICON_PX = 64, sc = ICON_PX / 24;
  const ix = SHADOW_PAD + (NODE_W - ICON_PX) / 2, iy = SHADOW_PAD + 24;   // icon centred above the label
  const px = SHADOW_PAD + NODE_W - 24, py = SHADOW_PAD + 24;              // health pip, top-right

  const defs = `<defs>` +
    (n.phantom ? `<filter id='gray'><feColorMatrix type='saturate' values='0'/></filter>` : '') +
  `</defs>`;

  // Card face: a flat white rounded rect with no border (the raised look comes from
  // the canvas drop shadow beneath). Phantom cards have no shadow, so they keep a
  // faint dashed rim.
  const face = n.phantom
    ? `<rect x='0' y='0' width='${NODE_W}' height='${NODE_H}' rx='16' ` +
        `fill='#eef1f4' stroke='${CARD_BORDER}' stroke-width='1'${dash}/>`
    : `<rect x='0' y='0' width='${NODE_W}' height='${NODE_H}' rx='16' fill='#ffffff'/>`;

  const icon = n.phantom
    ? `<g transform='translate(${ix},${iy}) scale(${sc})' opacity='0.5' filter='url(#gray)'>${iconInner}</g>`
    : `<g transform='translate(${ix},${iy}) scale(${sc})'>${iconInner}</g>`;

  const pip =
    `<circle cx='${px}' cy='${py}' r='8' fill='#ffffff'/>` +
    `<circle cx='${px}' cy='${py}' r='6.4' fill='${dot}'${n.phantom ? " fill-opacity='0.45'" : ''}/>`;

  const svg =
    `<svg xmlns='http://www.w3.org/2000/svg' width='${CARD_SVG_W}' height='${CARD_SVG_H}' ` +
      `viewBox='0 0 ${CARD_SVG_W} ${CARD_SVG_H}'>${defs}${face}${icon}${pip}</svg>`;
  return 'data:image/svg+xml,' + encodeURIComponent(svg);
}

// the ZONE MODEL: top-down bands, AUTO-SIZED to their content
type ZoneId = 'access' | 'apis' | 'storage' | 'compute' | 'security' | 'observability';

interface ZoneMeta { title: string; cols: number; }   // cols = MAX columns
const ZONE_META: Record<ZoneId, ZoneMeta> = {
  access:        { title: 'ACCESS · ENDPOINTS',      cols: 6 },
  apis:          { title: 'APIS · GATEWAY',          cols: 3 },
  storage:       { title: 'STORAGE',                 cols: 3 },
  compute:       { title: 'COMPUTE · SERVING · ML',  cols: 3 },
  security:      { title: 'SECURITY',                cols: 3 },
  observability: { title: 'OPS · CONTROL PLANE',     cols: 2 },
};
const ALL_ZONES: ZoneId[] = ['access', 'apis', 'storage', 'compute', 'security', 'observability'];
const BACKEND_ORDER: ZoneId[] = ['storage', 'compute', 'security', 'observability'];

// tier to zone fallback (only used if a node arrives without a server `zone`).
const TIER_ZONE: Record<string, ZoneId> = {
  dashboard: 'access', api: 'apis',
  storage: 'storage',
  compute: 'compute', engine: 'compute', ml: 'compute', serving: 'compute',
  orchestrator: 'compute', platform: 'compute',
  security: 'security', external: 'security',
  ops: 'observability', observability: 'observability',
};
const NODE_ZONE: Record<string, ZoneId> = {
  'headlamp': 'observability', 'kubernetes-api': 'observability',
  'fayda-replica': 'security', 'nhdw-ollama': 'compute',
};
// stable in-zone ordering so the layout never jitters (extras/ephemerals sort last)
const ZONE_PREF: Record<ZoneId, string[]> = {
  access: ['admin', 'internal-bi', 'public-dashboard', 'workshop', 'dbt-docs'],
  apis: ['nhdw-gateway'],
  storage: ['postgres-vault', 'seaweedfs-filer', 'clickhouse'],
  compute: ['nhdw-trino', 'airflow', 'mlflow', 'nhdw-ollama', 'jupyterhub'],
  security: ['keycloak', 'opa', 'fayda-replica'],
  observability: ['kubernetes-api', 'headlamp', 'prometheus', 'loki', 'alertmanager', 'promtail'],
};

function zoneOf(n: TopoNode): ZoneId {
  const z = (n.zone || undefined) as ZoneId | undefined;
  if (z && z in ZONE_META) return z;
  return NODE_ZONE[n.id] || TIER_ZONE[n.tier] || 'compute';
}

// layout
interface Pt { x: number; y: number; }
interface PlacedZone { id: ZoneId; title: string; bx: number; by: number; bw: number; bh: number; cols: number; }
interface LayoutOut {
  pos: Map<string, Pt>;
  zones: PlacedZone[];
  bounds: { x: number; y: number; w: number; h: number };
}

// rectangle geometry for the router obstacles + anti-cross post-check
interface Rect { l: number; r: number; t: number; b: number; }

function zoneDims(count: number, maxCols: number): { cols: number; rows: number; bw: number; bh: number } {
  const cols = Math.max(1, Math.min(maxCols, count || 1));
  const rows = Math.max(1, Math.ceil((count || 1) / cols));
  const innerW = cols * NODE_W + (cols - 1) * COL_GAP;
  const innerH = rows * NODE_H + (rows - 1) * ROW_GAP;
  return { cols, rows, bw: innerW + 2 * ZONE_PAD_X, bh: innerH + ZONE_PAD_TOP + ZONE_PAD_BOT };
}

function placeNodesInZone(z: PlacedZone, list: TopoNode[], pos: Map<string, Pt>): void {
  const cols = z.cols;
  const rows = Math.max(1, Math.ceil(list.length / cols));
  const cellW = NODE_W + COL_GAP, cellH = NODE_H + ROW_GAP;
  const ax = z.bx + ZONE_PAD_X + HW;
  const ay = z.by + ZONE_PAD_TOP + HH;
  list.forEach((node, i) => {
    const r = Math.floor(i / cols), c = i % cols;
    const inRow = (r === rows - 1) ? (list.length - r * cols) : cols;
    const rowOff = (cols - inRow) * cellW / 2;   // centre a partial last row
    pos.set(node.id, { x: ax + rowOff + c * cellW, y: ay + r * cellH });
  });
}

function computeLayout(nodes: TopoNode[]): LayoutOut {
  const byZone: Record<ZoneId, TopoNode[]> = {
    access: [], apis: [], storage: [], compute: [], security: [], observability: [],
  };
  for (const n of nodes) byZone[zoneOf(n)].push(n);
  for (const zid of ALL_ZONES) {
    const pref = ZONE_PREF[zid];
    byZone[zid].sort((a, b) => {
      const ia = pref.indexOf(a.id), ib = pref.indexOf(b.id);
      if (ia !== -1 || ib !== -1) return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
      return a.id.localeCompare(b.id);
    });
  }

  const dims = {} as Record<ZoneId, ReturnType<typeof zoneDims>>;
  for (const zid of ALL_ZONES) dims[zid] = zoneDims(byZone[zid].length, ZONE_META[zid].cols);

  const backend = BACKEND_ORDER.filter(z => byZone[z].length > 0);
  const accessBw = byZone.access.length ? dims.access.bw : 0;
  const apisBw = byZone.apis.length ? dims.apis.bw : 0;
  let backendW = 0;
  backend.forEach((z, i) => { backendW += dims[z].bw + (i ? ZONE_GAP : 0); });
  const totalW = Math.max(accessBw, apisBw, backendW, NODE_W + 2 * ZONE_PAD_X);

  const zones: PlacedZone[] = [];
  const pos = new Map<string, Pt>();
  let cursorY = MARGIN;

  // ACCESS band (top): a centred full-width row of the SPAs / endpoints
  if (byZone.access.length) {
    const { bw, bh, cols } = dims.access;
    const z: PlacedZone = { id: 'access', title: ZONE_META.access.title, bx: MARGIN + (totalW - bw) / 2, by: cursorY, bw, bh, cols };
    zones.push(z); placeNodesInZone(z, byZone.access, pos); cursorY = z.by + bh + BAND_GAP;
  }
  // APIS band (middle): the gateway, BETWEEN access and everything behind it
  if (byZone.apis.length) {
    const { bw, bh, cols } = dims.apis;
    const z: PlacedZone = { id: 'apis', title: ZONE_META.apis.title, bx: MARGIN + (totalW - bw) / 2, by: cursorY, bw, bh, cols };
    zones.push(z); placeNodesInZone(z, byZone.apis, pos); cursorY = z.by + bh + BAND_GAP;
  }
  // BACKEND region (bottom): storage · compute · security · ops, side by side
  if (backend.length) {
    const by = cursorY;
    let x = MARGIN + (totalW - backendW) / 2;
    for (const zid of backend) {
      const { bw, bh, cols } = dims[zid];
      const z: PlacedZone = { id: zid, title: ZONE_META[zid].title, bx: x, by, bw, bh, cols };
      zones.push(z); placeNodesInZone(z, byZone[zid], pos); x += bw + ZONE_GAP;
    }
  }

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const z of zones) {
    minX = Math.min(minX, z.bx); minY = Math.min(minY, z.by);
    maxX = Math.max(maxX, z.bx + z.bw); maxY = Math.max(maxY, z.by + z.bh);
  }
  if (!isFinite(minX)) { minX = 0; minY = 0; maxX = NODE_W; maxY = NODE_H; }
  return {
    pos, zones,
    bounds: { x: minX - MARGIN, y: minY - MARGIN, w: (maxX - minX) + 2 * MARGIN, h: (maxY - minY) + 2 * MARGIN },
  };
}

// Zone rectangles are pass-through for routing: avoiding whole borders makes
// edges twist. Only the tight bounding rect of the zone title text is an
// obstacle (expanded by TEXT_OBST_MARGIN in the router), so titles stay
// readable. The title paints in screen space at (+14, +20 baseline) with a
// zoom-clamped font, so its graph-space footprint is the screen metrics scaled
// by 1/zoom, evaluated at the estimated fit zoom `zEst` (zooming in only
// shrinks it).
function zoneTitleRect(z: PlacedZone, zEst: number): Rect {
  const inv = 1 / Math.max(0.2, Math.min(1, zEst));
  const w = Math.min(z.bw - 12 * inv, (14 + z.title.length * TITLE_CHAR_PX + 4) * inv);
  return { l: z.bx + 10 * inv, r: z.bx + w, t: z.by, b: z.by + 26 * inv };
}
function allTitleRects(zones: PlacedZone[], zEst: number): Array<{ id: string; rc: Rect }> {
  return zones.map(z => ({ id: `__title__${z.id}`, rc: zoneTitleRect(z, zEst) }));
}

/** True iff segment a to b passes through rect (shrunk by `pad`). */
function segHitsRect(ax: number, ay: number, bx: number, by: number, rc: Rect, pad = 0): boolean {
  const l = rc.l - pad, r = rc.r + pad, t = rc.t - pad, b = rc.b + pad;
  if (l >= r || t >= b) return false;
  if ((ax < l && bx < l) || (ax > r && bx > r) || (ay < t && by < t) || (ay > b && by > b)) return false;
  const inside = (x: number, y: number) => x >= l && x <= r && y >= t && y <= b;
  if (inside(ax, ay) || inside(bx, by)) return true;
  const segSeg = (x1: number, y1: number, x2: number, y2: number,
                  x3: number, y3: number, x4: number, y4: number): boolean => {
    const d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3);
    if (Math.abs(d) < 1e-9) return false;
    const ua = ((x4 - x3) * (y1 - y3) - (y4 - y3) * (x1 - x3)) / d;
    const ub = ((x2 - x1) * (y1 - y3) - (y2 - y1) * (x1 - x3)) / d;
    return ua >= 0 && ua <= 1 && ub >= 0 && ub <= 1;
  };
  return segSeg(ax, ay, bx, by, l, t, r, t) || segSeg(ax, ay, bx, by, r, t, r, b) ||
         segSeg(ax, ay, bx, by, r, b, l, b) || segSeg(ax, ay, bx, by, l, b, l, t);
}

/** Drop intermediate points that are collinear with their neighbours. */
function dropCollinear(poly: Pt[]): Pt[] {
  const out: Pt[] = [poly[0]];
  for (let i = 1; i < poly.length - 1; i++) {
    const a = out[out.length - 1], b = poly[i], c = poly[i + 1];
    if ((a.x === b.x && b.x === c.x) || (a.y === b.y && b.y === c.y)) continue;
    out.push(b);
  }
  out.push(poly[poly.length - 1]);
  return out;
}

/** Post-route straightening. The grid A* leaves staircase jogs and lane-offset
 *  detours even in open space. This pass greedily replaces any sub-path p[i..j]
 *  with a straight run or a single-L (one turn) connection when that shortcut
 *  hits no obstacle, so an edge bends only where something is in the way. The
 *  first and last segments (port stubs leaving a card) are preserved. */
function simplifyOrtho(poly: Pt[], rects: Array<{ id: string; rc: Rect }>,
                       exclude: Set<string>): Pt[] {
  let pts = dropCollinear(poly);
  if (pts.length <= 3) return pts;
  let changed = true;
  let guard = 24;                                   // bounded; polylines are short
  while (changed && guard-- > 0) {
    changed = false;
    // keep index 0 and 1 (port stub) and the last two points fixed
    outer:
    for (let i = 1; i < pts.length - 2; i++) {
      for (let j = pts.length - 2; j > i + 1; j--) {
        const a = pts[i], b = pts[j];
        const cands: Pt[][] = (a.x === b.x || a.y === b.y)
          ? [[a, b]]
          : [[a, { x: a.x, y: b.y }, b], [a, { x: b.x, y: a.y }, b]];
        for (const cand of cands) {
          if (polyHitsNodes(cand, rects, exclude) === 0) {
            pts = dropCollinear([...pts.slice(0, i), ...cand, ...pts.slice(j + 1)]);
            changed = true;
            break outer;
          }
        }
      }
    }
  }
  return pts;
}

function polyHitsNodes(poly: Pt[], rects: Array<{ id: string; rc: Rect }>, exclude: Set<string>): number {
  let hits = 0;
  for (let i = 1; i < poly.length; i++) {
    for (const { id, rc } of rects) {
      if (exclude.has(id)) continue;
      if (segHitsRect(poly[i - 1].x, poly[i - 1].y, poly[i].x, poly[i].y, rc, -1.5)) { hits++; break; }
    }
  }
  return hits;
}

// Hanan-grid orthogonal router
// Grid lines run through every node port coordinate and every obstacle-rect
// edge; routing is A* over the grid intersections with cost = segment length +
// TURN_PENALTY per 90° bend. The only hard obstacles are the zone title-text
// rects (expanded by TEXT_OBST_MARGIN); placement boxes and node cards are
// pass-through (edges paint under the card layer). Each edge is a
// multi-source/multi-target search over its two cards' four side-midpoint
// ports, so the cheapest (portA, portB) pair wins and the line enters its target
// at the nearest clean port. One search per edge settles all 16 pairs.

const SIDE_DIR: Record<Side, Pt> = { R: { x: 1, y: 0 }, L: { x: -1, y: 0 }, D: { x: 0, y: 1 }, U: { x: 0, y: -1 } };
const ALL_SIDES: Side[] = ['R', 'L', 'U', 'D'];
const DIR_VECS: Pt[] = [{ x: 1, y: 0 }, { x: -1, y: 0 }, { x: 0, y: 1 }, { x: 0, y: -1 }]; // R L D U

/** side-midpoint port + its stub launch point (one clean perpendicular step). */
function portOf(p: Pt, side: Side): { port: Pt; stub: Pt } {
  const d = SIDE_DIR[side];
  return {
    port: { x: p.x + d.x * HW, y: p.y + d.y * HH },
    stub: { x: p.x + d.x * (HW + PORT_STUB), y: p.y + d.y * (HH + PORT_STUB) },
  };
}

interface HananCtx {
  xs: number[]; ys: number[];          // sorted unique grid-line coordinates
  xIdx: Map<number, number>; yIdx: Map<number, number>;
  hBlocked: Uint8Array;                // horizontal segment (xi to xi+1, yi) blocked
  vBlocked: Uint8Array;                // vertical segment (xi, yi to yi+1) blocked
  hSoft: Uint8Array;                   // horizontal segment runs under a card (soft cost multiplier)
  vSoft: Uint8Array;                   // vertical segment runs under a card (soft cost multiplier)
  obst: Rect[];                        // the expanded title-text rects
  cards: Rect[];                       // card rects (soft-cost + straightening guard)
}

function buildHanan(pos: Map<string, Pt>, zones: PlacedZone[], bounds: LayoutOut['bounds'], zEst: number): HananCtx {
  const obst: Rect[] = allTitleRects(zones, zEst).map(({ rc }) => ({
    l: rc.l - TEXT_OBST_MARGIN, r: rc.r + TEXT_OBST_MARGIN,
    t: rc.t - TEXT_OBST_MARGIN, b: rc.b + TEXT_OBST_MARGIN,
  }));
  // strict card interiors (2px in from the border): running ALONG a border
  // line or a centre line outside the card is never penalised
  const cards: Rect[] = [...pos.values()].map(p => ({
    l: p.x - HW + 2, r: p.x + HW - 2, t: p.y - HH + 2, b: p.y + HH - 2,
  }));
  const q = (v: number) => Math.round(v * 2) / 2;   // quantise so identical lines dedupe
  const xsSet = new Set<number>(), ysSet = new Set<number>();
  for (const p of pos.values()) {
    xsSet.add(q(p.x)); xsSet.add(q(p.x - HW - PORT_STUB)); xsSet.add(q(p.x + HW + PORT_STUB));
    ysSet.add(q(p.y)); ysSet.add(q(p.y - HH - PORT_STUB)); ysSet.add(q(p.y + HH + PORT_STUB));
  }
  for (const o of obst) { xsSet.add(q(o.l)); xsSet.add(q(o.r)); ysSet.add(q(o.t)); ysSet.add(q(o.b)); }
  for (const z of zones) {   // zone edges + surrounding gap midlines = natural corridors
    xsSet.add(q(z.bx - ZONE_GAP / 2)); xsSet.add(q(z.bx + z.bw + ZONE_GAP / 2));
    ysSet.add(q(z.by - BAND_GAP / 2)); ysSet.add(q(z.by + z.bh + BAND_GAP / 2));
  }
  xsSet.add(q(bounds.x)); xsSet.add(q(bounds.x + bounds.w));
  ysSet.add(q(bounds.y)); ysSet.add(q(bounds.y + bounds.h));

  const xs = [...xsSet].sort((a, b) => a - b);
  const ys = [...ysSet].sort((a, b) => a - b);
  const xIdx = new Map(xs.map((v, i) => [v, i]));
  const yIdx = new Map(ys.map((v, i) => [v, i]));

  // Block any unit grid segment whose midpoint falls inside an obstacle rect
  // (obstacle edges are themselves grid lines, so hugging the margin is legal);
  // soft-mark any segment whose midpoint runs under a card interior.
  const inRects = (rs: Rect[], x: number, y: number) =>
    rs.some(o => x > o.l + 0.25 && x < o.r - 0.25 && y > o.t + 0.25 && y < o.b - 0.25);
  const hBlocked = new Uint8Array(xs.length * ys.length);
  const vBlocked = new Uint8Array(xs.length * ys.length);
  const hSoft = new Uint8Array(xs.length * ys.length);
  const vSoft = new Uint8Array(xs.length * ys.length);
  for (let yi = 0; yi < ys.length; yi++)
    for (let xi = 0; xi < xs.length - 1; xi++) {
      const mx = (xs[xi] + xs[xi + 1]) / 2, my = ys[yi];
      if (inRects(obst, mx, my)) hBlocked[yi * xs.length + xi] = 1;
      if (inRects(cards, mx, my)) hSoft[yi * xs.length + xi] = 1;
    }
  for (let xi = 0; xi < xs.length; xi++)
    for (let yi = 0; yi < ys.length - 1; yi++) {
      const mx = xs[xi], my = (ys[yi] + ys[yi + 1]) / 2;
      if (inRects(obst, mx, my)) vBlocked[yi * xs.length + xi] = 1;
      if (inRects(cards, mx, my)) vSoft[yi * xs.length + xi] = 1;
    }

  return { xs, ys, xIdx, yIdx, hBlocked, vBlocked, hSoft, vSoft, obst, cards };
}

const q05 = (v: number) => Math.round(v * 2) / 2;

/** Route one edge s to t: multi-source Dijkstra (A* with h=0, multi-target) over
 *  the Hanan grid from all 4 source-port stubs to all 4 target-port stubs.
 *  Returns the min-cost polyline [portA, stubA, …grid…, stubB, portB] plus the
 *  winning sides. Never fails hard: falls back to a mid-point Z if the grid is
 *  somehow fully blocked between the pair. */
function hananRoute(ctx: HananCtx, s: Pt, t: Pt): { poly: Pt[]; sSide: Side; tSide: Side } {
  const { xs, ys, xIdx, yIdx, hBlocked, vBlocked, hSoft, vSoft } = ctx;
  const W = xs.length, N = W * ys.length;
  const stateOf = (xi: number, yi: number, d: number) => (yi * W + xi) * 4 + d;

  const gScore = new Float64Array(N * 4).fill(Infinity);
  const cameFrom = new Int32Array(N * 4).fill(-1);
  const srcSide = new Int8Array(N * 4).fill(-1);   // which source side seeded this state

  const heap: Array<[number, number]> = [];
  const push = (f: number, st: number) => {
    heap.push([f, st]); let i = heap.length - 1;
    while (i > 0) { const p = (i - 1) >> 1; if (heap[p][0] <= heap[i][0]) break; [heap[p], heap[i]] = [heap[i], heap[p]]; i = p; }
  };
  const pop = (): [number, number] => {
    const top = heap[0], last = heap.pop()!;
    if (heap.length) { heap[0] = last; let i = 0;
      for (;;) { let l = i * 2 + 1, r = l + 1, m = i;
        if (l < heap.length && heap[l][0] < heap[m][0]) m = l;
        if (r < heap.length && heap[r][0] < heap[m][0]) m = r;
        if (m === i) break; [heap[m], heap[i]] = [heap[i], heap[m]]; i = m; } }
    return top;
  };

  // seed: each source-port stub, facing outward (turning right away costs a bend)
  const dirIndexOf = (d: Pt) => DIR_VECS.findIndex(v => v.x === d.x && v.y === d.y);
  for (let si = 0; si < ALL_SIDES.length; si++) {
    const side = ALL_SIDES[si];
    const { stub } = portOf(s, side);
    const xi = xIdx.get(q05(stub.x)), yi = yIdx.get(q05(stub.y));
    if (xi == null || yi == null) continue;
    const st = stateOf(xi, yi, dirIndexOf(SIDE_DIR[side]));
    gScore[st] = 0; srcSide[st] = si; push(0, st);
  }

  // targets: each target-port stub; arriving must end heading into the card
  // (dir = opposite of the port's outward dir) or pay one extra turn.
  const targets = new Map<number, { side: Side; inDir: number }>();   // cellIndex to info
  for (const side of ALL_SIDES) {
    const { stub } = portOf(t, side);
    const xi = xIdx.get(q05(stub.x)), yi = yIdx.get(q05(stub.y));
    if (xi == null || yi == null) continue;
    const d = SIDE_DIR[side];
    targets.set(yi * W + xi, { side, inDir: dirIndexOf({ x: -d.x, y: -d.y }) });
  }

  let best: { cost: number; state: number; side: Side } | null = null;
  const done = new Set<number>();
  while (heap.length) {
    const [f, st] = pop();
    if (f > gScore[st] + TURN_PENALTY) continue;              // stale heap entry
    if (best && f >= best.cost) break;                        // cannot improve
    const d = st & 3, cell = st >> 2, xi = cell % W, yi = (cell / W) | 0;
    const tInfo = targets.get(cell);
    if (tInfo && !done.has(st)) {
      done.add(st);
      const total = gScore[st] + (d === tInfo.inDir ? 0 : TURN_PENALTY);
      if (!best || total < best.cost) best = { cost: total, state: st, side: tInfo.side };
    }
    const gc = gScore[st];
    for (let nd = 0; nd < 4; nd++) {
      const v = DIR_VECS[nd];
      const nxi = xi + v.x, nyi = yi + v.y;
      if (nxi < 0 || nxi >= W || nyi < 0 || nyi >= ys.length) continue;
      if (v.x !== 0 && hBlocked[yi * W + Math.min(xi, nxi)]) continue;
      if (v.y !== 0 && vBlocked[Math.min(yi, nyi) * W + xi]) continue;
      const soft = v.x !== 0 ? hSoft[yi * W + Math.min(xi, nxi)] : vSoft[Math.min(yi, nyi) * W + xi];
      const len = v.x !== 0 ? Math.abs(xs[nxi] - xs[xi]) : Math.abs(ys[nyi] - ys[yi]);
      const ng = gc + len * (soft ? CARD_CROSS_FACTOR : 1) + (nd !== d ? TURN_PENALTY : 0);
      const ns = stateOf(nxi, nyi, nd);
      if (ng < gScore[ns]) { gScore[ns] = ng; cameFrom[ns] = st; srcSide[ns] = srcSide[st]; push(ng, ns); }
    }
  }

  if (best) {
    const pts: Pt[] = [];
    let cur = best.state;
    while (cur !== -1) {
      const cell = cur >> 2;
      pts.push({ x: xs[cell % W], y: ys[(cell / W) | 0] });
      cur = cameFrom[cur];
    }
    pts.reverse();
    const sSide = ALL_SIDES[srcSide[best.state]] ?? 'R';
    const tSide = best.side;
    const sp = portOf(s, sSide), tp = portOf(t, tSide);
    return { poly: dropCollinear([sp.port, ...pts, tp.port]), sSide, tSide };
  }

  // fallback (grid exhausted, practically unreachable): stub-to-stub Z
  const sSide: Side = t.x >= s.x ? 'R' : 'L';
  const tSide: Side = t.x >= s.x ? 'L' : 'R';
  const sp = portOf(s, sSide), tp = portOf(t, tSide);
  const mx = (sp.stub.x + tp.stub.x) / 2;
  return {
    poly: dropCollinear([sp.port, sp.stub, { x: mx, y: sp.stub.y }, { x: mx, y: tp.stub.y }, tp.stub, tp.port]),
    sSide, tSide,
  };
}

/** Spread the edges that share one (node, side) into parallel lanes along that
 *  side (sorted by where the OTHER end sits, so lanes don't cross each other),
 *  shifting the port point and its adjacent stub segment together. Straight
 *  2-point runs are left untouched (offsetting one end would break H/V), and
 *  an offset is rejected when the shifted stub segment would land inside a
 *  title-text obstacle the router avoided. */
function spreadLanes(
  routed: Array<{ e: TopoEdge; poly: Pt[]; sSide: Side; tSide: Side }>,
  pos: Map<string, Pt>,
  obst: Rect[],
): void {
  type EndRef = { r: (typeof routed)[number]; end: 's' | 't' };
  const groups = new Map<string, EndRef[]>();
  for (const r of routed) {
    groups.set(`${r.e.source}|${r.sSide}`, [...(groups.get(`${r.e.source}|${r.sSide}`) ?? []), { r, end: 's' }]);
    groups.set(`${r.e.target}|${r.tSide}`, [...(groups.get(`${r.e.target}|${r.tSide}`) ?? []), { r, end: 't' }]);
  }
  for (const [key, list] of groups) {
    if (list.length < 2) continue;
    const [nodeId, side] = key.split('|') as [string, Side];
    const p = pos.get(nodeId); if (!p) continue;
    const horizPort = side === 'R' || side === 'L';    // port varies along y for R/L, x for U/D
    const otherPt = (er: EndRef): Pt => {
      const poly = er.r.poly;
      return er.end === 's' ? poly[poly.length - 1] : poly[0];
    };
    list.sort((a, b) => horizPort ? otherPt(a).y - otherPt(b).y : otherPt(a).x - otherPt(b).x);
    const span = (horizPort ? NODE_H : NODE_W) - 2 * PORT_INSET;
    const gap = Math.min(LANE_GAP, span / Math.max(1, list.length - 1));
    list.forEach((er, i) => {
      const off = (i - (list.length - 1) / 2) * gap;
      if (off === 0) return;
      const poly = er.r.poly;
      if (poly.length < 3) return;                     // straight run: keep as-is
      const a = er.end === 's' ? 0 : poly.length - 1;  // port point
      const b = er.end === 's' ? 1 : poly.length - 2;  // adjacent stub/bend point
      let na: Pt, nb: Pt;
      if (horizPort) {
        if (poly[a].y !== poly[b].y) return;           // stub not perpendicular: skip
        na = { x: poly[a].x, y: poly[a].y + off };
        nb = { x: poly[b].x, y: poly[b].y + off };
      } else {
        if (poly[a].x !== poly[b].x) return;
        na = { x: poly[a].x + off, y: poly[a].y };
        nb = { x: poly[b].x + off, y: poly[b].y };
      }
      // never shift a stub into a title-text obstacle (also guard the segment
      // to the NEXT bend, which slides with the stub)
      const c = er.end === 's' ? Math.min(2, poly.length - 1) : Math.max(poly.length - 3, 0);
      const nc = poly[c];
      for (const rc of obst) {
        if (segHitsRect(na.x, na.y, nb.x, nb.y, rc, 0)) return;
        if (segHitsRect(nb.x, nb.y, nc.x, nc.y, rc, 0)) return;
      }
      poly[a] = na; poly[b] = nb;
    });
  }
}

// render model per edge
interface RenderEdge {
  model: TopoEdge;
  poly: Pt[];
  width: number;
  colorKey: 'good' | 'watch' | 'alert' | 'info' | 'struct';
  traffic: boolean;
  flowing: boolean;
  spawn: boolean;
  reqRate: number;
}

function edgeWidth(e: TopoEdge): number {
  const rr = e.traffic?.reqRate ?? 0;
  if (e.traffic && (e.traffic.samples ?? 0) > 0) return 2.2 + Math.min(4.2, Math.log2(1 + rr) * 1.5);
  return 1.8;
}
function edgeColorKey(e: TopoEdge): RenderEdge['colorKey'] {
  if (e.kind === 'spawns') return 'info';
  const hasTraffic = !!(e.traffic && (e.traffic.samples ?? 0) > 0);
  if (e.status === 'down') return 'alert';
  if (e.status === 'degraded') return 'watch';
  if (hasTraffic) {
    const er = e.traffic!.errRate ?? 0;
    return er > 0.05 ? 'alert' : er > 0.01 ? 'watch' : 'good';
  }
  return 'struct';
}

function buildRouteModel(graph: TopoGraph, layout: LayoutOut, zEst: number): RenderEdge[] {
  const ctx = buildHanan(layout.pos, layout.zones, layout.bounds, zEst);
  const nodeIds = new Set(graph.nodes.map(n => n.id));

  const uniq: TopoEdge[] = [];
  const seen = new Set<string>();
  for (const e of graph.edges) {
    if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) continue;
    const key = `${e.source}|${e.target}`;
    if (seen.has(key)) continue;
    seen.add(key);
    uniq.push(e);
  }

  // 1) route every edge over the Hanan grid; the cheapest (portA, portB) pair wins
  const routed: Array<{ e: TopoEdge; poly: Pt[]; sSide: Side; tSide: Side }> = [];
  for (const e of uniq) {
    const s = layout.pos.get(e.source), t = layout.pos.get(e.target);
    if (!s || !t) continue;
    const r = hananRoute(ctx, s, t);
    routed.push({ e, poly: r.poly, sSide: r.sSide, tSide: r.tSide });
  }

  // 2) fan multi-edge (node, side) groups into tidy parallel lanes
  spreadLanes(routed, layout.pos, ctx.obst);

  // 3) straighten: a shortcut may remove bends but must not cross a title
  //    (hard) or cut under a card the A* went around (soft):
  //    both rect sets guard the pass, with the edge's own two cards excluded.
  //    Then verify: count any title crossing that survived (should be zero).
  const titleObst = ctx.obst.map((rc, i) => ({ id: `__title__${i}`, rc }));
  const cardObst = graph.nodes
    .filter(n => layout.pos.has(n.id))
    .map(n => { const p = layout.pos.get(n.id)!; return { id: n.id, rc: { l: p.x - HW + 2, r: p.x + HW - 2, t: p.y - HH + 2, b: p.y + HH - 2 } }; });
  const straightenObst = [...titleObst, ...cardObst];
  const none = new Set<string>();
  const out: RenderEdge[] = [];
  const unresolved: string[] = [];
  for (const r of routed) {
    const exclude = new Set([r.e.source, r.e.target]);
    const poly = simplifyOrtho(dropCollinear(r.poly), straightenObst, exclude);
    if (polyHitsNodes(poly, titleObst, none) > 0) unresolved.push(`${r.e.source}->${r.e.target}`);
    const e = r.e;
    const hasTraffic = !!(e.traffic && (e.traffic.samples ?? 0) > 0);
    out.push({
      model: e, poly, width: edgeWidth(e), colorKey: edgeColorKey(e),
      traffic: hasTraffic, flowing: hasTraffic && (e.traffic!.reqRate ?? 0) > 0,
      spawn: e.kind === 'spawns',
      reqRate: e.traffic?.reqRate ?? 0,
    });
  }
  if (unresolved.length > 0) console.warn(`[topology] ${unresolved.length}/${out.length} edge(s) still cross a zone title after routing: ${unresolved.join(', ')}`);
  return out;
}

// node stylesheet classes
function healthClass(n: TopoNode): string {
  if (n.phantom) return 'phantom';
  if (n.kind === 'external' || n.tier === 'external') return 'ext';
  switch (n.health) {
    case 'up':       return 'h-up';
    case 'degraded': return 'h-degraded';
    case 'down':     return 'h-down';
    default:         return 'h-unknown';
  }
}
function accentClass(n: TopoNode): string {
  if (n.ephemeral) return 'ephemeral';
  if (n.id === 'kubernetes-api') return 'ctrl';
  if (n.tier === 'api' || n.kind === 'api') return 'api';
  return '';
}

function buildNodeElements(graph: TopoGraph, layout: LayoutOut, theme: TopoTheme): any[] {
  const els: any[] = [];
  for (const n of graph.nodes) {
    const p = layout.pos.get(n.id);
    if (!p) continue;
    els.push({
      // The card (face, icon, pip) is entirely inside bgIcon; cytoscape adds
      // only the state rings.
      data: { id: n.id, label: n.label, model: n, bgIcon: nodeBgUri(n, theme) },
      position: { x: p.x, y: p.y },
      classes: `node ${healthClass(n)} ${accentClass(n)}`.trim(),
    });
  }
  return els;
}

// main entry
export function renderTopology(
  mountEl: HTMLElement,
  graph: TopoGraph,
  theme: TopoTheme,
  handlers: TopoHandlers = {},
): TopologyHandle {
  // Off-white canvas with a faint square grid of crossed hairlines (ink-4 at
  // partial alpha).
  mountEl.style.background = '#f6f7f8';
  const gridLine = hexToRgba(theme.ink4, 0.13);
  mountEl.style.backgroundImage =
    `linear-gradient(${gridLine} 1px, transparent 1px), ` +
    `linear-gradient(90deg, ${gridLine} 1px, transparent 1px)`;
  mountEl.style.backgroundSize = '24px 24px';
  mountEl.style.position = mountEl.style.position || 'relative';
  // Double-rule frame around the widget: a 5px white border between thin steel
  // lines outside and inside.
  mountEl.style.border = '5px solid #ffffff';
  mountEl.style.borderRadius = '14px';
  mountEl.style.boxShadow = '0 0 0 1.5px #c3cdd9, inset 0 0 0 1.5px #c3cdd9, 0 10px 30px -14px rgba(15,23,42,0.28)';
  mountEl.style.boxSizing = 'border-box';

  const tone = (k: RenderEdge['colorKey']): string =>
    k === 'alert' ? theme.alert : k === 'watch' ? theme.watch :
    k === 'good' ? theme.good : k === 'info' ? theme.info : theme.ink4;  // neutral net lines slightly darker

  // overlay canvas (zones + shadows + orthogonal edges), UNDER the cards
  const canvas = document.createElement('canvas');
  canvas.style.position = 'absolute';
  canvas.style.left = '0'; canvas.style.top = '0';
  canvas.style.width = '100%'; canvas.style.height = '100%';
  canvas.style.pointerEvents = 'none';
  canvas.style.zIndex = '0';
  mountEl.appendChild(canvas);
  const ctx = canvas.getContext('2d')!;

  // card stylesheet (colours from tokens; icon+title only)
  const style: any[] = [
    {
      selector: 'node.node',
      style: {
        shape: 'round-rectangle',
        width: NODE_W, height: NODE_H,
        'background-color': theme.paper,
        'background-opacity': 1 as any,
        'background-image': 'data(bgIcon)',
        'background-fit': 'none',
        // Image painted with clip:none so any SHADOW_PAD margin is not clipped;
        // the white rect maps to the node box.
        'background-width': CARD_SVG_W as any,
        'background-height': CARD_SVG_H as any,
        'background-position-x': '50%',
        'background-position-y': '50%',
        'background-clip': 'none',
        'border-width': 0,                      // any rim is drawn inside the card SVG
        label: 'data(label)',
        'font-family': theme.fontSans,
        'font-size': 16,
        'font-weight': 700 as any,
        'text-valign': 'center',
        'text-halign': 'center',
        'text-margin-y': 40 as any,
        'text-max-width': (NODE_W - 22) as any,
        'text-wrap': 'wrap',
        color: theme.ink,                       // dark label on the white button
        'z-index': 10,
        'transition-property': 'opacity, border-width',
        'transition-duration': 120 as any,
      } as any,
    },
    // Health state rings, drawn by cytoscape on the card edge. Healthy, unknown,
    // external and ephemeral cards get no ring (only the SVG and its pip); only
    // real problems ring in colour, so status stays legible at a glance.
    {
      selector: 'node.h-degraded',
      style: { 'border-width': 2, 'border-color': theme.watch,
               'underlay-color': theme.watch, 'underlay-opacity': 0.16 as any, 'underlay-padding': 8 as any, 'underlay-shape': 'round-rectangle' } as any,
    },
    {
      selector: 'node.h-down',
      style: { 'border-width': 2.2, 'border-color': theme.alert,
               'underlay-color': theme.alert, 'underlay-opacity': 0.2 as any, 'underlay-padding': 8 as any, 'underlay-shape': 'round-rectangle' } as any,
    },
    // phantom (declared, no live pod): the SVG already renders it flat, dashed
    // and grey with a desaturated icon; here the whole card is dimmed.
    { selector: 'node.phantom', style: { 'border-width': 0, opacity: 0.6, color: theme.ink3 } as any },
    { selector: 'node.node:selected', style: { 'border-width': 3, 'border-color': theme.info } as any },
    { selector: 'node.dim', style: { opacity: 0.16 } as any },
  ];

  // Estimated FIT zoom: sizes the screen-space title text in graph units for
  // the router obstacles (see zoneTitleRect). Falls back to a typical wide
  // container before first layout.
  const estFitZoom = (b: LayoutOut['bounds']): number => {
    const vw = mountEl.clientWidth || 1500, vh = mountEl.clientHeight || 680;
    return Math.max(0.2, Math.min(1, Math.min((vw - 48) / b.w, (vh - 48) / b.h)));
  };

  let layout = computeLayout(graph.nodes);
  let routes = buildRouteModel(graph, layout, estFitZoom(layout.bounds));

  const cy = cytoscape({
    container: mountEl,
    elements: buildNodeElements(graph, layout, theme),
    style,
    layout: { name: 'preset' },
    minZoom: 0.22,
    maxZoom: 2.4,
    userZoomingEnabled: false,   // zoom by buttons only, so the wheel scrolls the page
    boxSelectionEnabled: false,
    autoungrabify: true,
  });

  // paint the cy card canvases above the overlay (zones, shadows, edges under cards)
  Array.from(mountEl.querySelectorAll('canvas')).forEach(cnv => {
    if (cnv !== canvas) (cnv as HTMLElement).style.zIndex = '1';
  });

  function fitToBounds(animate: boolean) {
    const b = layout.bounds;
    const vw = cy.width(), vh = cy.height();
    const pad = 24;
    const z = Math.max(cy.minZoom(), Math.min(cy.maxZoom(),
      Math.min((vw - pad * 2) / b.w, (vh - pad * 2) / b.h)));
    const panx = (vw - b.w * z) / 2 - b.x * z;
    const pany = (vh - b.h * z) / 2 - b.y * z;
    if (animate) cy.animate({ zoom: z, pan: { x: panx, y: pany } } as any, { duration: 220 });
    else { cy.zoom(z); cy.pan({ x: panx, y: pany }); }
  }
  cy.ready(() => { resizeCanvas(); fitToBounds(false); });

  let hoverId: string | null = null;
  let selEdgeId: string | null = null;
  let selNodeId: string | null = null;
  const antOffset = new Map<string, number>();

  function resizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const w = mountEl.clientWidth, h = mountEl.clientHeight;
    canvas.width = Math.max(1, Math.round(w * dpr));
    canvas.height = Math.max(1, Math.round(h * dpr));
  }

  function roundRect(x: number, y: number, w: number, h: number, r: number) {
    r = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function neighbourOf(id: string): Set<string> {
    const s = new Set<string>([id]);
    for (const re of routes) {
      if (re.model.source === id) s.add(re.model.target);
      if (re.model.target === id) s.add(re.model.source);
    }
    return s;
  }

  function paint() {
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const W = canvas.width / dpr, H = canvas.height / dpr;
    ctx.clearRect(0, 0, W, H);

    const z = cy.zoom(); const pan = cy.pan();
    const TX = (x: number) => x * z + pan.x;
    const TY = (y: number) => y * z + pan.y;

    // 1) zone boundaries and titles: the box border uses the zone hue; the
    //    fill stays transparent.
    for (const zn of layout.zones) {
      const x = TX(zn.bx), y = TY(zn.by), w = zn.bw * z, h = zn.bh * z;
      const hue = ZONE_HUES[zn.id];
      ctx.strokeStyle = hexToRgba(hue.base, 0.85);
      ctx.lineWidth = 1.6; ctx.setLineDash([7 * Math.max(0.6, z), 5 * Math.max(0.6, z)]);  // dashed zone boundary
      roundRect(x, y, w, h, 14); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = '#4a6b8f';   // muted steel-blue zone titles
      ctx.font = `600 ${Math.max(9.5, Math.min(12.5, 10 * z + 2))}px ${theme.fontSans}`;
      ctx.textBaseline = 'alphabetic';
      ctx.textAlign = 'left';
      ctx.fillText(zn.title, x + 14, y + 20);
    }

    // 2) Node drop shadows, drawn with the canvas shadow API (a browser gaussian
    //    without the clipping and corner artefacts of a baked SVG filter). This
    //    canvas sits under the opaque cytoscape cards, so each card's rounded-rect
    //    footprint is stroked only to cast its shadow. Phantom cards get none.
    ctx.save();
    ctx.shadowColor = 'rgba(15,23,42,0.34)';
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = 9 * Math.max(0.5, z);
    ctx.shadowBlur = 20 * Math.max(0.5, z);
    ctx.fillStyle = '#ffffff';
    const cw = NODE_W * z, ch = NODE_H * z, cr = Math.max(6, 16 * z);
    for (const [id, p] of layout.pos) {
      const model = cy.getElementById(id).data('model');
      if (!model || model.phantom) continue;
      roundRect(TX(p.x) - cw / 2, TY(p.y) - ch / 2, cw, ch, cr);
      ctx.fill();
    }
    ctx.restore();

    // 3) orthogonal edges (dim non-focus when hovering a card)
    const focus = hoverId ? neighbourOf(hoverId) : null;
    for (const re of routes) {
      const inFocus = !focus || (focus.has(re.model.source) && focus.has(re.model.target));
      const selected = re.model.id === selEdgeId;
      const col = selected ? theme.info : tone(re.colorKey);
      const alpha = inFocus ? (re.colorKey === 'struct' ? 0.45 : 0.7) : 0.08;  // faint, so lines do not dominate
      ctx.strokeStyle = hexToRgba(col, alpha);
      ctx.lineWidth = (selected ? re.width + 1.4 : re.width);
      ctx.lineJoin = 'round'; ctx.lineCap = 'round';
      if (re.traffic) ctx.setLineDash([9 * Math.max(0.6, z), 7 * Math.max(0.6, z)]);
      else if (re.spawn) ctx.setLineDash([4 * Math.max(0.6, z), 4 * Math.max(0.6, z)]);
      else ctx.setLineDash([]);
      ctx.lineDashOffset = re.traffic ? -(antOffset.get(re.model.id) ?? 0) : 0;
      const p = re.poly;
      ctx.beginPath();
      // Straight H/V runs; each 90° turn is a quarter-arc. The radius is
      // clamped per-corner to half the shorter adjacent segment so a tight pair
      // of turns can never produce the "curve followed by a sharp kink" artifact.
      const R = Math.max(5, Math.min(10, 9 * z));
      const sx = (i: number) => TX(p[i].x), sy = (i: number) => TY(p[i].y);
      const segLen = (i: number, j: number) => Math.hypot(sx(j) - sx(i), sy(j) - sy(i));
      ctx.moveTo(sx(0), sy(0));
      for (let i = 1; i < p.length - 1; i++) {
        const r = Math.min(R, segLen(i - 1, i) / 2 - 0.5, segLen(i, i + 1) / 2 - 0.5);
        if (r >= 1.5) ctx.arcTo(sx(i), sy(i), sx(i + 1), sy(i + 1), r);
        else ctx.lineTo(sx(i), sy(i));
      }
      ctx.lineTo(sx(p.length - 1), sy(p.length - 1));
      ctx.stroke();
      ctx.setLineDash([]);
      const endAlpha = inFocus ? 1 : 0.12;
      const rPort = Math.max(2, 2.6 * Math.min(1.3, z));
      ctx.fillStyle = hexToRgba(col, endAlpha);
      ctx.beginPath();
      ctx.arc(TX(p[0].x), TY(p[0].y), rPort, 0, Math.PI * 2);
      ctx.fill();
      if (p.length >= 2) {
        const a = p[p.length - 2], b = p[p.length - 1];
        const ang = Math.atan2(b.y - a.y, b.x - a.x);
        const sz = 8;
        const bx = TX(b.x), by = TY(b.y);
        ctx.fillStyle = hexToRgba(col, endAlpha);
        ctx.beginPath();
        ctx.moveTo(bx, by);
        ctx.lineTo(bx - sz * Math.cos(ang - 0.5), by - sz * Math.sin(ang - 0.5));
        ctx.lineTo(bx - sz * Math.cos(ang + 0.5), by - sz * Math.sin(ang + 0.5));
        ctx.closePath(); ctx.fill();
      }
    }
  }

  // animation loop: advance ants + repaint (stays synced to cy transform)
  let raf = 0;
  let last = performance.now();
  function frame(now: number) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    for (const re of routes) {
      if (!re.flowing) continue;
      const speed = 22 + Math.min(120, Math.log2(1 + re.reqRate) * 42);
      antOffset.set(re.model.id, (antOffset.get(re.model.id) ?? 0) + speed * dt);
    }
    paint();
    raf = requestAnimationFrame(frame);
  }
  raf = requestAnimationFrame(frame);

  // interactions
  cy.on('mouseover', 'node.node', (evt: any) => {
    hoverId = evt.target.id();
    const nb = neighbourOf(hoverId);
    cy.nodes().forEach((n: any) => { if (!nb.has(n.id())) n.addClass('dim'); });
  });
  cy.on('mouseout', 'node.node', () => { hoverId = null; cy.nodes().removeClass('dim'); });

  cy.on('tap', 'node.node', (evt: any) => {
    selEdgeId = null; selNodeId = evt.target.id();
    handlers.onNodeSelect?.(evt.target.data('model'));
  });
  cy.on('tap', (evt: any) => {
    if (evt.target !== cy) return;
    const pos = evt.position || { x: 0, y: 0 };
    const thr = 7 / cy.zoom();
    let best: RenderEdge | null = null, bestD = thr;
    for (const re of routes) {
      const p = re.poly;
      for (let i = 1; i < p.length; i++) {
        const d = ptSeg(pos.x, pos.y, p[i - 1].x, p[i - 1].y, p[i].x, p[i].y);
        if (d < bestD) { bestD = d; best = re; }
      }
    }
    if (best) { selEdgeId = best.model.id; selNodeId = null; cy.$(':selected').unselect(); handlers.onEdgeSelect?.(best.model); }
    else { selEdgeId = null; selNodeId = null; cy.$(':selected').unselect(); handlers.onBackgroundTap?.(); }
  });

  function ptSeg(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
    const vx = bx - ax, vy = by - ay;
    const wx = px - ax, wy = py - ay;
    const c1 = vx * wx + vy * wy;
    if (c1 <= 0) return Math.hypot(px - ax, py - ay);
    const c2 = vx * vx + vy * vy;
    if (c2 <= c1) return Math.hypot(px - bx, py - by);
    const t = c1 / c2;
    return Math.hypot(px - (ax + t * vx), py - (ay + t * vy));
  }

  const ro = new ResizeObserver(() => { resizeCanvas(); paint(); });
  ro.observe(mountEl);

  // test/debug bridge: expose cy and the routed model so automated tests can
  // drive selection and assert routing invariants (harness + regression use)
  (mountEl as any).__cyTopo = cy;
  (mountEl as any).__topoDebug = () => ({ layout, routes });

  // the callable handle
  const handle = (() => {
    if (raf) cancelAnimationFrame(raf);
    ro.disconnect();
    cy.destroy();
    if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
  }) as TopologyHandle;

  handle.zoomIn = () => cy.animate({ zoom: Math.min(cy.zoom() * 1.3, cy.maxZoom()), center: { eles: cy.elements('node.node') } }, { duration: 150 });
  handle.zoomOut = () => cy.animate({ zoom: Math.max(cy.zoom() / 1.3, cy.minZoom()), center: { eles: cy.elements('node.node') } }, { duration: 150 });
  handle.fit = () => fitToBounds(true);
  handle.resetLayout = () => {
    layout = computeLayout(graph.nodes); routes = buildRouteModel(graph, layout, estFitZoom(layout.bounds));
    cy.batch(() => cy.nodes('.node').forEach((n: any) => { const p = layout.pos.get(n.id()); if (p) n.position(p); }));
    fitToBounds(true);
  };
  handle.select = (id: string | null) => {
    cy.elements().unselect(); selEdgeId = null;
    if (id) { const n = cy.getElementById(id); if (n.nonempty()) { n.select(); selNodeId = id;
      cy.animate({ center: { eles: n }, zoom: Math.max(cy.zoom(), 0.9) } as any, { duration: 200 }); } }
  };
  handle.update = (g: TopoGraph) => {
    graph = g;
    layout = computeLayout(g.nodes);
    routes = buildRouteModel(g, layout, estFitZoom(layout.bounds));
    const z = cy.zoom(); const p = cy.pan();
    cy.batch(() => { cy.elements().remove(); cy.add(buildNodeElements(g, layout, theme)); });
    cy.zoom(z); cy.pan(p);
    if (selNodeId) { const n = cy.getElementById(selNodeId); if (n.nonempty()) n.select(); }
    paint();
  };

  return handle;
}
