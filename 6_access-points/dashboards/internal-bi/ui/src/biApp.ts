/**
 * Internal-BI shell state layer.
 *
 * Every page is catalog-driven (src/widgets/* via CatalogPage +
 * runtime/PageRenderer), so this holds only the shell state the React app
 * needs: page/filter/alert state, the resolved permission spec (loadSpec,
 * grants, data_url helpers) and the reactive subscribe() bridge.
 * renderActive() is a no-op dispatch (biApp draws nothing itself).
 *
 * `this.$nextTick(fn)` maps to queueMicrotask, and reactivity is a minimal
 * subscribe() callback React consumes (useBiApp.ts). Mutations to
 * active/filters/openFilter/modal flags call notify().
 *
 * @ts-nocheck: JavaScript-style state object.
 */
// @ts-nocheck
/* eslint-disable */

// Reference tables come from _shared/reference.ts, which fills them from the
// active country profile at boot, so there is one region list and it matches
// the warehouse.
import {
  REGION_OPTIONS as REGIONS, REGION_BY_ID, PROGRAMMES, DISEASES, DISEASE_NAME,
  COMMODITIES, scopeLabels,
} from './widgets/_shared/reference';

import { T } from './theme';
import { fetchDashboardSpec, SPEC_DENIED } from './api/spec';
import { authFetch } from './api/useAuth';

declare global {
  interface Window {
    __biApp?: any;
  }
}

// Reference data exposed to Alpine state
// Region filter values are the CODES the marts key on (region_id); the name is
// the display label. The global-filter + AlertModal emit {code} as the value so a
// wildcard user's pick ('OR') matches the mart, exactly like a scoped user's cap.
// 2-letter region codes used across the marts (mart region_id) to SPA region names.
// Same coding as the MFR/IDSR sources (WOR-<REG>-… org-unit codes).
// Programme filter values are the mart programme_id CODES (uppercase); the name is
// the display label. Same code-as-value contract as REGIONS so a wildcard pick
// ('MALARIA') matches programme_id in the marts.
const PERIOD_OPTS = { 'all':'All time','last_4wk':'Last 4 weeks','last_12wk':'Last 12 weeks','last_6mo':'Last 6 months','last_12mo':'Last 12 months','last_24mo':'Last 24 months','ytd':'Year to date','efy_ytd':'Fiscal year to date' };
// Commodity filter values are the exact mart commodity_name strings: the code is
// the name because the supply marts (bi_supply.mart_supply_region_stock /
// mart_resource_allocation) key on commodity_name directly. Kept in sync with the
// filter catalog (bundle/seed-pack/app/security/filters.yaml, id: commodity) and the
// fetchers' commodity_col narrowing. A PLAIN narrowing filter (like period): no
// role scope, always the full list.


function biApp() {
  return {
    active: 'overview',
    pages: [
      {id: 'overview', ico: 'i-grid',         title: 'Overview'},
      {id: 'disease',  ico: 'i-alert',        title: 'Disease Surveillance'},
      {id: 'resource', ico: 'i-package',      title: 'Resource Management'},
      {id: 'control-tower', ico: 'i-truck',   title: 'Supply Control Tower'},
      {id: 'service',  ico: 'i-chart-line',   title: 'Service Performance'},
      {id: 'predictions', ico: 'i-brain',     title: 'Predictions'},
      {id: 'export',   ico: 'i-upload',       title: 'Data Export & Query'},
      // Hidden from sidebar: opened from the user chip in the top bar
      {id: 'settings', ico: 'i-settings',     title: 'Profile & Settings', hideInNav: true},
    ],

    // Alert subscriptions: real backend (/gw/alerts/subscriptions)
    // Self-subscription: "notify me when <metric> on <disease by geo> crosses
    // <threshold>". alertSubs is a cache of the caller's real subscriptions
    // (drives the topbar bell badge); the AlertModal owns the CRUD via authFetch.
    compactDensity: false,                         // Settings · wired to body.density-compact
    alertModal: false,
    // Modal form state: openSubscribe() pre-fills disease+geo from the click site.
    alertDisease: 'all',
    alertGeo: 'all',
    alertThresh: 0.6,
    alertNotify: 'realtime',  // realtime | daily | weekly
    alertSubs: [],            // real cache: the caller's own subscriptions
    // Fired notifications: real backend (/gw/alerts/notifications)
    // The evaluator (gateway + alert_evaluation DAG) writes an alert_notification
    // whenever a forecast crosses a subscription threshold. The topbar bell badge
    // shows the UNREAD count; the NotificationPanel lists them + marks read.
    alertNotifications: [],   // real cache: the caller's own fired alerts
    alertNotifUnread: 0,      // unread count to bell badge
    notifPanel: false,        // NotificationPanel open flag
    /** Fetch the caller's alert subscriptions to alertSubs, then notify React
     *  (the modal / st-alert-subs list). */
    async loadAlertSubs() {
      try {
        const r = await authFetch('/gw/alerts/subscriptions');
        this.alertSubs = r.ok ? await r.json() : [];
      } catch { this.alertSubs = []; }
      notify();
    },
    /** Fetch the caller's fired notifications + unread count, which drives the bell
     *  badge and the NotificationPanel. */
    async loadAlertNotifications() {
      try {
        const r = await authFetch('/gw/alerts/notifications');
        const j = r.ok ? await r.json() : { notifications: [], unread_count: 0 };
        this.alertNotifications = j.notifications || [];
        this.alertNotifUnread = j.unread_count || 0;
      } catch { this.alertNotifications = []; this.alertNotifUnread = 0; }
      notify();
    },
    setNotifPanel(v) {
      this.notifPanel = !!v;
      if (v) this.loadAlertNotifications();   // refresh on open
      notify();
    },
    async markNotificationRead(id) {
      try { await authFetch(`/gw/alerts/notifications/${id}/read`, { method: 'POST' }); }
      catch { /* best-effort: a reload shows the current state */ }
      await this.loadAlertNotifications();
    },
    async markAllNotificationsRead() {
      try { await authFetch('/gw/alerts/notifications/read-all', { method: 'POST' }); }
      catch { /* best-effort */ }
      await this.loadAlertNotifications();
    },
    /** Context-aware subscribe: pre-fill the modal with the caller's disease +
     *  geography (from the click site) so the intent reads "alert me about this". */
    openSubscribe(diseaseKey, geoKey) {
      this.alertDisease = diseaseKey || 'all';
      this.alertGeo     = geoKey     || 'all';
      this.alertModal = true;
      notify();
    },
    diseaseLabelForSub(key) {
      if (key === 'all' || !key) return 'All diseases';
      return DISEASE_NAME[key] || key;
    },
    notifyLabelForSub(key) {
      return { realtime: 'Real-time', daily: 'Daily digest', weekly: 'Weekly summary' }[key] || key;
    },

    // Global filter state is page-scoped: each page keeps its own slice, so a
    // region set on one page never bleeds into another. `filters`
    // is a getter that returns the ACTIVE page's slice (lazily created).
    _filtersByPage: {} as Record<string, { geography: string; period: string; programme: string; [k: string]: string }>,
    get filters(): { geography: string; period: string; programme: string; [k: string]: string } {
      const p = (this.active as string) || 'overview';
      return (this._filtersByPage[p] ||
        (this._filtersByPage[p] = { geography: 'all', period: 'all', programme: 'all' }));
    },
    filtersExpanded: false,
    openFilter: null,
    prgSearch: '',
    PERIOD_OPTS, REGIONS, PROGRAMMES, DISEASES, DISEASE_NAME, COMMODITIES,

    currentPage() {
      return this.pages.find(p => p.id === this.active);
    },

    setFilter(k, v) {
      this.filters[k] = v;
      this.openFilter = null;
      this.$nextTick(() => this.renderActive());
    },
    filterLabel(key) {
      const v = this.filters[key];
      if (key === 'geography') return v === 'all' ? scopeLabels.allGeography : (REGIONS.find(r => r.code === v)?.name || v);
      if (key === 'period')    return PERIOD_OPTS[v] || v;
      if (key === 'programme') {
        if (v === 'all') return 'All programmes';
        const pg = PROGRAMMES.find(p => p.code === v);
        if (pg) return pg.name;
        return DISEASE_NAME[v] || v;
      }
      return v;
    },

    init() {
      // Load the resolved permission spec (carries per-widget data_urls) so
      // catalog widgets can fetch their mart data.
      this.loadSpec();
      // Load the caller's alert subscriptions (modal / st-alert-subs) and
      // fired notifications (topbar bell badge + NotificationPanel).
      this.loadAlertSubs();
      this.loadAlertNotifications();
      // Expose this Alpine instance globally so the AI panel can find it without DOM walking
      window.__biApp = this;
      this.$nextTick(()=>this.renderActive());
      window.addEventListener('resize', ()=>{ this.renderActive() });
    },

    // data_url-first render seam
    // The resolved permission spec (gateway) carries a data_url per granted
    // widget. Catalog widgets (src/widgets/*) fetch that mart data and render
    // an explicit empty state when there is none, never synthetic rows.
    _spec: null,
    _specLoaded: false,  // true only when the gateway spec resolved: grants apply
    _specDataUrls: {},   // widget_id to gateway data_url (page-scoped wins)
    _grantedWidgets: null,  // Set<widget_id> present in the spec (grant ≠ data_url)
    _grantedPages: null,    // Set<'internal-bi/<page>'> present in the spec
    _widgetData: {},     // widget_id to Promise<rowObjects[] | null> (session cache)
    async loadSpec() {
      const spec = await fetchDashboardSpec('internal-bi');
      // Denied is not degraded: the gateway answered and this role has no
      // access to this dashboard. Replace the page with a denial screen and
      // render nothing else to an unauthorized caller.
      if (spec === SPEC_DENIED) {
        const publicUrl = window.location.protocol + '//' +
          window.location.host.replace(/^[^.]+/, 'public');
        document.body.innerHTML = `
          <div style="font-family:'Inter',system-ui,sans-serif;min-height:100vh;display:flex;
                      align-items:center;justify-content:center;background:var(--paper,#f7f5f1)">
            <div style="max-width:480px;background:#fff;border:1px solid #e3ddd2;border-radius:14px;
                        padding:38px;box-shadow:0 6px 30px rgba(0,0,0,.07)">
              <h1 style="margin:0 0 10px;font-size:21px">No access to Internal BI</h1>
              <p style="line-height:1.6;font-size:15px;color:#5a5248">Your role doesn't grant any
                 pages on this dashboard. If you believe you need access, request it through the
                 Public Dashboard's access-request form.</p>
              <a href="${publicUrl}" style="display:inline-block;margin-top:12px;padding:10px 18px;
                 border-radius:9px;background:#1d3557;color:#fff;text-decoration:none;font-size:14px">
                 Go to Public Dashboard</a>
            </div>
          </div>`;
        return;
      }
      // Spec fetch failed (gateway hiccup / 5xx / network blip at boot):
      // _specLoaded stays false and everything renders on the degraded
      // fallback. The global filter bar renders from the spec, so without a
      // retry a one-off boot failure would hide the Period/Programme/Region
      // chips for the whole session. Retry with backoff so the app recovers
      // as soon as the gateway answers.
      if (!spec) {
        this._specRetryN = (this._specRetryN || 0) + 1;
        if (this._specRetryN <= 30) {
          clearTimeout(this._specRetryT);
          this._specRetryT = setTimeout(() => this.loadSpec(), Math.min(15000, 2000 * this._specRetryN));
        }
        return;
      }
      this._specRetryN = 0;
      this._spec = spec;
      const urls = {};
      // Grant = presence in the resolved spec, INDEPENDENT of data_url:
      // a granted-but-unbound widget still renders via its synthetic path.
      const grantedWidgets = new Set();
      const grantedPages = new Set();
      for (const w of (spec.widgets || [])) {
        grantedWidgets.add(w.id);
        if (w.data_url) urls[w.id] = w.data_url;
      }
      for (const p of (spec.pages || [])) {
        grantedPages.add(p.id);
        for (const w of (p.widgets || [])) {
          grantedWidgets.add(w.id);
          if (w.data_url) urls[w.id] = w.data_url;
        }
      }
      this._specDataUrls = urls;
      this._grantedWidgets = grantedWidgets;
      this._grantedPages = grantedPages;
      this._specLoaded = true;
      this._widgetData = {};   // invalidate pre-spec cache entries (all null)
      // Never leave the user on a non-granted page (default is 'disease',
      // which some roles don't have): hop to their first granted nav page.
      if (!this.pageGranted(this.active)) {
        const first = this.pages.find(p => !p.hideInNav && this.pageGranted(p.id));
        if (first) this.active = first.id;
      }
      notify();   // React re-gates pages/nav on the loaded grants
      this.$nextTick(() => this.renderActive());
    },

    /** Is this widget granted by the resolved spec? A widget ABSENT from the
     *  spec is not granted and must not render at all: synthetic data must
     *  never stand in for content the role isn't allowed to see. Before a
     *  spec resolves everything is treated as granted (degraded fallback). */
    widgetGranted(widgetId) {
      return !this._specLoaded || this._grantedWidgets.has(widgetId);
    },
    /** Same grant check for pages · biApp nav ids are short ('service'),
     *  the spec/catalog namespaces them ('internal-bi/service'). */
    pageGranted(pageId) {
      return !this._specLoaded || this._grantedPages.has(`internal-bi/${pageId}`);
    },

    /** Fetch a widget's real data through the gateway (auth'd, via the /gw
     *  proxy). Resolves an array of row OBJECTS, or null when the widget has
     *  no data_url, the request fails, or the gateway returns a permitted-stub
     *  / empty result. Never rejects: null means "use the synthetic path". */
    widgetData(widgetId) {
      if (!(widgetId in this._widgetData)) {
        const url = this._specDataUrls[widgetId];
        this._widgetData[widgetId] = !url ? Promise.resolve(null) :
          authFetch(url.replace(/^\/api\//, '/gw/'))
            .then(r => (r.ok ? r.json() : null))
            .then(payload => {
              const d = payload?.data;
              if (!d || !Array.isArray(d.rows) || d.rows.length === 0) return null;
              return this.rowsToObjects(d.columns || [], d.rows);
            })
            .catch(() => null);
      }
      return this._widgetData[widgetId];
    },

    /** Same seam as widgetData(), but appends query params to the widget's
     *  data_url (e.g. the Alarm-Signal model selector re-fetching with
     *  ?model=ears|outbreak|seasonal). Cached per (widget_id + query) so each
     *  model keeps its own resolved rows. Null when unbound / failed / empty:
     *  the caller renders the synthetic fallback (seam contract unchanged). */
    widgetDataParam(widgetId, params) {
      const base = this._specDataUrls[widgetId];
      if (!base) return Promise.resolve(null);
      const qs = Object.entries(params || {})
        .filter(([, v]) => v != null && v !== '')
        .map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v))
        .join('&');
      const url = qs ? base + (base.includes('?') ? '&' : '?') + qs : base;
      const cacheKey = widgetId + '?' + qs;
      if (!(cacheKey in this._widgetData)) {
        this._widgetData[cacheKey] = authFetch(url.replace(/^\/api\//, '/gw/'))
          .then(r => (r.ok ? r.json() : null))
          .then(payload => {
            const d = payload?.data;
            if (!d || !Array.isArray(d.rows) || d.rows.length === 0) return null;
            return this.rowsToObjects(d.columns || [], d.rows);
          })
          .catch(() => null);
      }
      return this._widgetData[cacheKey];
    },

    /** {columns, rows} to array of row objects. The gateway already returns
     *  dict-shaped rows (trino_client zips column names in); tolerate
     *  positional-array rows too so the seam survives a fetcher change. */
    rowsToObjects(columns, rows) {
      const names = (columns || []).map(c => (typeof c === 'string' ? c : (c?.name ?? c?.id)));
      return (rows || []).map(r => Array.isArray(r)
        ? Object.fromEntries(names.map((n, i) => [n, r[i]]))
        : r);
    },

    // small shared helpers for the live adapters
    /** '2026W9' / '2026-W09' to sortable ordinal (DHIS2 weekly periods are not
     *  zero-padded, so lexical sort breaks across W9 to W10). */
    _isoWeekOrd(w) {
      const m = /^(\d{4})-?W(\d{1,2})$/.exec(String(w || ''));
      return m ? (+m[1]) * 100 + (+m[2]) : 0;
    },
    /** Mart region_id (2-letter) to SPA region name; passes through unknowns. */
    _regionName(id) { return REGION_BY_ID[id] || id || 'n/a'; },

    renderActive() {
      // Authorization gate: App.tsx skips the JSX shell of non-granted
      // pages with the same check; nothing may be drawn for them either.
      if (!this.pageGranted(this.active)) return;
      const fn = ({
        // overview, disease, resource: catalog-driven (src/widgets/* via CatalogPage).
        overview: ()=>{},
        disease:  ()=>{},
        resource: ()=>{},
        // Catalog-driven (src/sections/CatalogPage to runtime/PageRenderer over
        // src/widgets/*); biApp draws nothing for these pages.
        service:  ()=>{},
        predictions: ()=>{},     // React-only page; biApp doesn't draw anything
        export:   ()=>{},
        settings: ()=>{},
      })[this.active];
      if (fn) fn.call(this);
    },


    sv: { _data: null, _widgetState: {} },
  };
}

// React subscription layer
// biApp() returns a plain object. We wrap it in a thin reactive proxy so
// React useSyncExternalStore() can subscribe to state changes triggered
// by any of biApp's setters. Triggered explicitly via `notify()` rather
// than via Proxy (which would over-fire on chart-rendering inner state).
const _subscribers = new Set<() => void>();
export function subscribe(fn: () => void): () => void {
  _subscribers.add(fn);
  return () => _subscribers.delete(fn);
}
export function notify(): void {
  for (const fn of _subscribers) fn();
}

let _instance: any = null;
export function getBiApp(): any {
  if (_instance) return _instance;
  _instance = biApp();
  // Patch in $nextTick (used heavily inside imperative methods to wait
  // for DOM render: we map to microtask since React's render is sync).
  _instance.$nextTick = (fn: () => void) => queueMicrotask(fn);
  // Patch every method that mutates display state to notify React.
  // Only top-level UI state needs to round-trip to React (active, openFilter,
  // alertModal, filters, etc.): the catalog widget runtime owns its own DOM.
  return _instance;
}

export function initBiApp(): any {
  const app = getBiApp();
  if (!(window as any).__biApp) {
    app.init();
  }
  return app;
}

export { REGIONS, PROGRAMMES, DISEASES, DISEASE_NAME, PERIOD_OPTS, COMMODITIES, scopeLabels };
