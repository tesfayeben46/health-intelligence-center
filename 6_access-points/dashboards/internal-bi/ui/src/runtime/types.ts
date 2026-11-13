/**
 * Widget-runtime contract for the internal-BI shell.
 *
 * Every widget is a self-contained folder under `src/widgets/<id>/` exporting a
 * default `WidgetDef`. The shell discovers widgets at build time (registry.ts);
 * the catalog (gateway spec) decides which ones render, in which section and at
 * what span. Adding a widget means adding a folder and a catalog row, with no
 * shell edit.
 *
 * A widget owns three things:
 *   1. how it draws itself: render(ctx)
 *   2. which filter dimensions it obeys: reactsTo (permission-scoped values
 *      arrive already capped by the gateway)
 *   3. how it talks to its neighbours: ctx.bus (named dimensions only)
 *
 * ctx.fetchData() returns real mart rows or
 * null, and null makes the host show an empty state.
 */
import type { ResolvedWidget, ResolvedSection, ResolvedPage } from '../api/spec';

/** Resolved design tokens handed to every widget (shell owns them; widgets
 *  express layout in terms of these, never hardcoded colours/fonts). */
export interface Theme {
  navy: Record<string, string>;      // navy-900..navy-50
  status: { good: string; watch: string; alert: string; info: string };
  ink: string[];                     // ink, ink-2, ink-3, ink-4
  paper: string;
  line: string;
  fontSans: string;
  fontMono: string;
  /** Amber sequential interpolator (0..1 to colour), shared by funnels,
   *  heatmaps and choropleths so every widget uses the same scale. */
  amber: (t: number) => string;
  /** Chart colour ramps from the design tokens, read by choropleth, heatmap
   *  and treemap widgets so every scale uses the same palette. */
  ramps: {
    scoreReliability: string[];
    diseaseBurden: string[];
    anomalyZScore: string[];
    supplyStress: string[];
    wastageTreemap: string[];
  };
}

/** A widget's real data, already shaped to row objects. null = no real data
 *  (unbound, denied at the mart, empty result or fetch error), which renders
 *  an empty state. */
export type WidgetData = Record<string, any>[] | null;

/** The current global-filter state (named dimensions). */
export interface FilterState {
  geography: string;
  programme: string;
  period: string;
  [dim: string]: string;
}

/** Cross-widget bus: pub/sub on named dimensions only (no free-form messages),
 *  so there is one state owner and every interaction is auditable. */
export interface FilterBus {
  get(): FilterState;
  set(dim: string, value: string): void;
  /** Subscribe to changes on any of `dims`. Returns an unsubscribe fn. */
  subscribe(dims: string[], cb: (s: FilterState) => void): () => void;
}

/** A master-detail route: which entity kind is being viewed and which id.
 *  The shell swaps the page for the matching DetailDef while a route is active
 *  (Projects list to openDetail('project', id) to Project detail to closeDetail()).
 *  `props` carries optional open-time context (e.g. the row already in hand) so
 *  the detail view can paint instantly, then confirm against fetchEntity. */
export interface DetailRoute {
  kind: string;                      // matches a registered DetailDef.kind
  id: string;                        // the selected entity's id
  props?: Record<string, any>;
}

/** A modal request: which ModalDef to open in the shell's single modal slot,
 *  plus its open-time props (a create form's defaults, an onDone callback, …). */
export interface ModalRequest {
  id: string;                        // matches a registered ModalDef.id
  props?: Record<string, any>;
}

/** Host API: what a widget can ask the shell to do (navigate for the
 *  assistant, focus a widget, open the alert modal, …).
 *
 *  openDetail/openModal and the current* getters form the master-detail and
 *  modal surface. They are inert on a plain grid page and take visible effect
 *  only under `renderAppShell`, which renders the registered detail or modal. */
export interface HostAPI {
  navigateToPage(pageId: string): void;
  focusWidget(widgetId: string): void;
  openSubscribe?(programme: string, geography: string): void;

  /** Master-detail: open a registered detail view for a selected entity. Under
   *  renderAppShell the shell swaps the page for the DetailDef whose kind matches. */
  openDetail(kind: string, id: string, props?: Record<string, any>): void;
  /** Leave the detail view and return to the page. */
  closeDetail(): void;
  /** The active detail route, or null when the page (not a detail) is showing. */
  readonly currentDetail: DetailRoute | null;

  /** Modal host: open a registered ModalDef in the shell's single modal slot.
   *  A widget triggers a create/submit/compute flow without the shell hardcoding
   *  it; pass an `onDone` in props to be called back on success. */
  openModal(id: string, props?: Record<string, any>): void;
  /** Close whatever modal is open (a no-op if none). */
  closeModal(): void;
  /** The open modal request, or null when none is open. */
  readonly currentModal: ModalRequest | null;
}

/** Everything a widget render receives. */
export interface WidgetCtx {
  /** The content mount. The widget fills it inside the card chrome the host
   *  already drew (title and description come from the catalog). */
  el: HTMLElement;
  widget: ResolvedWidget;            // catalog placement (id, data_url, col_span…)
  section?: ResolvedSection;         // absent for chrome widgets (filter bar etc.)
  page: ResolvedPage;                // owning/active page; chrome widgets read global_filters
  theme: Theme;
  filters: FilterState;
  bus: FilterBus;
  host: HostAPI;
  /** Fetch this widget's data (gateway to OPA to Trino). Optional params are
   *  appended to the data_url (e.g. a local model selector). A null result
   *  means render an empty state. */
  fetchData(params?: Record<string, string>): Promise<WidgetData>;
  /** Fetch rows from an ops REST endpoint: the non-mart entity path for CRUD
   *  and master-detail pages (e.g. `/gw/projects`, `/gw/artifacts`, `/gw/ml`).
   *  Those routes are authz-gated at the gateway. Returns rows or null (empty,
   *  denied or error). Do not route entity data through fetchData, which
   *  returns empty stubs for non-mart sources. */
  fetchEntity(path: string, params?: Record<string, string>): Promise<WidgetData>;
  /** Render the standard empty state into `el`, so every widget looks the
   *  same when it has no data. */
  empty(message?: string): void;
}

export interface WidgetDef {
  id: string;
  /** Named filter dimensions this widget re-renders on. The host uses them as
   *  the re-render set and shows an "unaffected by filters" badge when a widget
   *  declares none, so unfiltered data is never shown unlabelled. */
  reactsTo?: string[];
  /** Render with no card chrome: the widget sits directly on the page
   *  background and draws its own tiles (KPI strips, hero boards); a wrapping
   *  card around tiles would read as stacked cards. */
  frameless?: boolean;
  /** How this widget's card absorbs freed grid columns when a scope-hidden
   *  neighbour reflows the section:
   *    'width' (default): keep the natural height and grow wider (trend
   *                       charts, tables, KPI strips);
   *    'ratio':           preserve the card's aspect; the renderer scales the
   *                       card height with its width growth (map-like widgets;
   *                       chart instances follow via their resize observers). */
  reflow?: 'width' | 'ratio';
  /** Draw the widget. May return a cleanup fn (dispose charts / listeners). */
  render(ctx: WidgetCtx): void | (() => void) | Promise<void | (() => void)>;
}

/*
 * App-shell contracts: master-detail views and modals.
 *
 * The entity-CRUD counterparts to WidgetDef. They use the same registry style
 * (a folder per component, discovered at build time) and receive the same
 * inputs (fetchEntity, theme, host, empty), so a master-detail page is authored
 * the same way as a grid widget, with no shell edit.
 */

/** Everything a detail view receives: the widget inputs plus the active route.
 *  Detail views read through fetchEntity (never the mart path) and drive
 *  navigation and modals through host. */
export interface DetailCtx {
  /** The detail mount; the shell has drawn the surrounding frame. */
  el: HTMLElement;
  route: DetailRoute;                // { kind, id, props } being viewed
  page: ResolvedPage;                // owning page (scope/context)
  theme: Theme;
  filters: FilterState;
  bus: FilterBus;
  host: HostAPI;
  /** The non-mart entity fetch (see WidgetCtx.fetchEntity). */
  fetchEntity(path: string, params?: Record<string, string>): Promise<WidgetData>;
  /** Standard empty state into `el`. */
  empty(message?: string): void;
}

export interface DetailDef {
  /** Matches DetailRoute.kind / host.openDetail(kind, …). */
  kind: string;
  /** Draw the detail view. May return a cleanup fn. */
  render(ctx: DetailCtx): void | (() => void) | Promise<void | (() => void)>;
}

/** Everything a modal receives. The shell draws the backdrop + frame (with the
 *  title and a close affordance); the ModalDef fills the body. */
export interface ModalCtx {
  /** The modal body mount. */
  el: HTMLElement;
  /** Open-time props from host.openModal(id, props) (form defaults, onDone, …). */
  props: Record<string, any>;
  page: ResolvedPage;
  theme: Theme;
  host: HostAPI;
  /** The non-mart entity fetch (see WidgetCtx.fetchEntity). */
  fetchEntity(path: string, params?: Record<string, string>): Promise<WidgetData>;
  /** Shorthand for host.closeModal(). */
  close(): void;
  /** Standard empty state into `el`. */
  empty(message?: string): void;
}

export interface ModalDef {
  /** Matches ModalRequest.id / host.openModal(id, …). */
  id: string;
  /** Title drawn by the shell in the modal frame chrome. */
  title?: string;
  /** Draw the modal body. May return a cleanup fn. */
  render(ctx: ModalCtx): void | (() => void) | Promise<void | (() => void)>;
}
