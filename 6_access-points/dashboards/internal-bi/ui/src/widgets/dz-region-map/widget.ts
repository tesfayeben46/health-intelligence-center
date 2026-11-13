/**
 * Burden and readiness map (Disease page). Overlays response capacity on case
 * burden: a Burden / Readiness / Reliability toggle shows where high burden meets
 * low readiness. The metrics stay separate layers rather than one blended index,
 * so it is always clear which factor drives the colour.
 * Rendered at admin-1 level; a single-region scope drills to that region's woredas.
 * Source: bi_idsr.mart_idsr_disease_weekly plus Supply (readiness), DQ (reliability) and MFR.
 *
 * Layout:
 *   · header: subtitle and Burden/Readiness/Reliability segmented toggle
 *   · left:   choropleth map with a vertical legend overlaid on the land/water backdrop
 *   · right:  "Selected region" card (level badge, P(alarm), cases 7d, CFR, weekly
 *             sparkline) and ranked bars (top 5 regions, or woreda burden hotspots in
 *             a single-region scope)
 *   · bottom: highest, median and data freshness
 *
 * Region adaptation: the map always loads the national dataset (geography:'all';
 * OPA still row-filters to the caller's scope). When exactly one region is in
 * scope (a regional role whose rows collapse to one, or a region picked in the
 * global filter), the map zooms to it and the ranked list and summary pivot to
 * that region's woredas, since a one-region top 5 or median is meaningless.
 *
 * Data contract (dz-region-map fetcher), one row per region:
 *   region:str(id) · burden_incidence_100k:num(12wk cases/100k, workforce pop) ·
 *   readiness_pct:int(supply tracer >=1mo stock) · reliability_pct:int(DQ completeness) ·
 *   p_alarm:num(region peak forecast alarm) · cases_7d:int(latest week) ·
 *   cfr_pct:num(deaths/cases) · data_through:date(latest week)
 * ?spark=1: the scoped region's weekly {week, cases} series (sidebar sparkline).
 * ?woreda=1: one row per woreda in the scoped region (region=woreda_uid, woreda_name,
 *             burden_incidence_100k, cases_7d, cfr_pct, p_alarm) for the single-region pivot.
 * reactsTo: geography · programme.
 */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { choropleth, sparklineSvg } from '../_shared/charts';
import { loadRegionContextGeo } from '../_shared/geo';
import { regionName, COUNTRY, levelName } from '../_shared/reference';

type Metric = 'burden' | 'readiness' | 'reliability';
const META: Record<Metric, { col: string; label: string; icon: string; higherWorse: boolean; fmt: (v: number) => string; bands: string[] }> = {
  burden:      { col: 'burden_incidence_100k', label: 'Burden',      icon: 'i-chart-bar',    higherWorse: true,  fmt: v => `${Math.round(v)}`,      bands: ['Low', 'Moderate', 'Elevated', 'High', 'Very high'] },
  readiness:   { col: 'readiness_pct',         label: 'Readiness',   icon: 'i-shield-check', higherWorse: false, fmt: v => `${Math.round(v)}%`,     bands: ['Critical', 'Low', 'Fair', 'Good', 'Strong'] },
  reliability: { col: 'reliability_pct',       label: 'Reliability', icon: 'i-check-circle', higherWorse: false, fmt: v => `${Math.round(v)}%`,     bands: ['Critical', 'Low', 'Fair', 'Good', 'Strong'] },
};

// Burden ramp, low to high: green to yellow to red (low burden reads healthy, high
// reads as alarm). Local to this widget; drives the map, the overlaid legend and
// the ranked bars so they share one scale.
const BURDEN_RAMP = ['#2E9E5B', '#8CC152', '#F6C544', '#E8853A', '#C0392B'];

const esc = (s: any) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));
/** Hex or rgb()/rgba() to rgba tint for badge and bar backgrounds. theme.amber
 *  returns rgb() strings, so both forms are parsed; otherwise amber tints would
 *  come out opaque and hide the badge text. */
const tint = (c: string, a: number): string => {
  const h = /^#?([0-9a-f]{6})$/i.exec(c);
  if (h) { const n = parseInt(h[1], 16); return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`; }
  const r = /rgba?\(([^)]+)\)/i.exec(c);
  if (r) { const [rr, gg, bb] = r[1].split(',').map(x => parseInt(x.trim(), 10) || 0); return `rgba(${rr},${gg},${bb},${a})`; }
  return c;
};
/** "2026-06-02" (or a Date) to "Jun 2, 2026" / "Jun 2" (short). */
const fmtDate = (v: any, short = false): string => {
  const d = new Date(String(v).length <= 10 ? String(v) + 'T00:00:00' : String(v));
  if (isNaN(d.getTime())) return String(v ?? 'n/a');
  const mo = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getMonth()];
  return short ? `${mo} ${d.getDate()}` : `${mo} ${d.getDate()}, ${d.getFullYear()}`;
};
const median = (xs: number[]): number => {
  const s = [...xs].sort((a, b) => a - b); const n = s.length;
  return n ? (n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2) : 0;
};

const def: WidgetDef = {
  id: 'dz-region-map',
  reactsTo: ['geography', 'programme'],
  // Not reflow:'ratio'. The map host has a fixed height
  // (worldContext), so a scope reflow must not make the card taller. Region
  // zoom uses boundingCoords, independent of reflow.
  render(ctx: WidgetCtx) {
    const t = ctx.theme;
    let metric: Metric = 'burden';
    let chartCleanup: (() => void) | undefined;
    let rows: any[] = [];
    let rowsByName: Record<string, any> = {};
    let idByName: Record<string, string> = {};
    let woredaRows: any[] = [];       // single-region drill-down (burden per woreda)
    let dataThrough: any = null;
    let geo: any = null;
    let selectedName: string | null = null;
    let lastSparkRegion: string | null = null;

    // The global region filter drives the map focus and initial selection.
    const focusId = ctx.filters.geography;
    const focusName = focusId && focusId !== 'all' ? regionName(String(focusId)) : null;
    // The single region in scope (a picked region, or an OPA role cap to one
    // region). Resolved after the national fetch, whose rows may collapse to one.
    let soleName: string | null = focusName;
    let soleId: string | null = focusName ? String(focusId) : null;

    // Colour stops (low to high) per metric. The map ramp, the overlaid legend and
    // the ranked bars all read from these so the card uses one scale.
    const stopsFor = (m: Metric): string[] =>
      m === 'burden'
        ? BURDEN_RAMP
        : [t.amber(0), t.amber(0.34), t.amber(0.67), t.amber(1)];
    const colorAt = (stops: string[], f: number): string =>
      stops[Math.min(stops.length - 1, Math.max(0, Math.round(f * (stops.length - 1))))];
    // Severity tone for the selected-region badge, taken from the status colours
    // rather than the ramp (the pale end of a ramp gives unreadable badge text).
    // Burden higher = worse
    // (green to amber to red); readiness/reliability higher = better (red to amber to green).
    const bandTone = (m: typeof META[Metric], idx: number): string => {
      const tones = m.higherWorse
        ? [t.status.good, t.status.info, t.status.watch, t.status.watch, t.status.alert]
        : [t.status.alert, t.status.watch, t.status.watch, t.status.good, t.status.good];
      return tones[Math.min(tones.length - 1, Math.max(0, idx))];
    };

    ctx.el.innerHTML = `
      <style>
        .dzm{display:flex;flex-direction:column;height:100%;gap:.5rem;font-family:${t.fontSans}}
        .dzm-head{display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap}
        .dzm-sub{font-size:.78rem;color:${t.ink[2]};line-height:1.28;max-width:56ch}
        .dzm-seg .ico{margin-right:.32rem}
        /* map narrowed to ~0.9fr, metrics panel widened */
        .dzm-main{display:grid;grid-template-columns:minmax(0,0.82fr) 384px;gap:.7rem;flex:1;min-height:362px}
        .dzm-mapwrap{position:relative;min-width:0;min-height:350px;border-radius:16px;overflow:hidden;background:${t.paper};box-shadow:0 2px 10px rgba(15,23,42,.07),0 0 0 1px rgba(15,23,42,.04)}
        .dz-map-mount{width:100%;height:100%;min-height:350px}
        /* vertical burden legend, overlaid on the backdrop left of the map */
        .dzm-maplegend{position:absolute;left:14px;top:50%;transform:translateY(-50%);z-index:6;display:flex;flex-direction:column;align-items:center;gap:.34rem;padding:.46rem .4rem .4rem;border-radius:11px;background:rgba(255,255,255,.7);backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px);box-shadow:0 1px 5px rgba(15,23,42,.12),0 0 0 1px rgba(15,23,42,.05)}
        .dzm-maplegend-cap{font-size:.55rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:${t.ink[2]};max-width:5.4rem;text-align:center;line-height:1.1}
        .dzm-maplegend-scale{display:flex;flex-direction:column;align-items:center;gap:.26rem}
        .dzm-maplegend-bar{width:10px;height:104px;border-radius:6px;box-shadow:inset 0 0 0 1px rgba(15,23,42,.1)}
        .dzm-maplegend-hi,.dzm-maplegend-lo{font-size:.56rem;font-weight:600;letter-spacing:.02em;color:${t.ink[2]}}
        .dzm-side{display:flex;flex-direction:column;gap:.5rem;min-width:0}
        .dzm-card{border-radius:13px;background:${t.paper};padding:.56rem .68rem;box-shadow:0 1px 4px rgba(15,23,42,.06),0 0 0 1px rgba(15,23,42,.035)}
        .dzm-sel{display:flex;flex-direction:column}
        .dzm-eyebrow{font-size:.59rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:${t.ink[3]};margin-bottom:.3rem}
        .dzm-sel-top{display:flex;align-items:center;justify-content:space-between;gap:.5rem}
        .dzm-sel-name{display:flex;align-items:center;gap:.36rem;font-size:.95rem;font-weight:700;color:${t.ink[0]};overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
        .dzm-sel-name .ico{color:${t.status.alert}}
        .dzm-badge{flex-shrink:0;font-size:.64rem;font-weight:600;padding:.14rem .48rem;border-radius:999px;white-space:nowrap}
        .dzm-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:.4rem;margin-top:.5rem;text-align:center}
        .dzm-stat .k{font-size:.6rem;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:${t.ink[3]}}
        .dzm-stat .v{font-size:.98rem;font-weight:700;color:${t.ink[0]};font-variant-numeric:tabular-nums;line-height:1.2;margin-top:.05rem}
        /* calmer sparkline: spacing instead of a divider line */
        .dzm-spark{margin-top:.5rem}
        .dzm-spark-svg{line-height:0}
        .dzm-spark-cap{display:flex;align-items:center;justify-content:space-between;font-size:.6rem;color:${t.ink[3]};margin-top:.1rem}
        .dzm-top5{flex:1;display:flex;flex-direction:column;min-height:0}
        .dzm-top5-hd{font-size:.73rem;font-weight:600;color:${t.ink[1]};margin-bottom:.36rem;display:flex;align-items:center;justify-content:space-between;gap:.4rem}
        .dzm-top5-hd .u{font-size:.6rem;font-weight:500;color:${t.ink[3]};white-space:nowrap}
        .dzm-top5-list{display:flex;flex-direction:column;gap:.16rem;flex:1;justify-content:center}
        .dzm-row{display:grid;grid-template-columns:70px 1fr 40px;align-items:center;gap:.5rem;padding:.05rem .28rem;border-radius:6px;font-size:.73rem;line-height:1.15}
        .dzm-row.sel{background:${tint(t.status.alert, 0.08)}}
        .dzm-row .n{color:${t.ink[1]};overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
        .dzm-row.sel .n{color:${t.status.alert};font-weight:600}
        .dzm-row .track{height:7px;border-radius:5px;background:${t.navy['50']};overflow:hidden}
        .dzm-row .track > span{display:block;height:100%;border-radius:5px}
        .dzm-row .val{text-align:right;font-weight:700;color:${t.ink[0]};font-variant-numeric:tabular-nums}
        /* summary strip: softer dividers, more padding */
        .dzm-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:0;border-radius:13px;background:${t.paper};box-shadow:0 1px 4px rgba(15,23,42,.06),0 0 0 1px rgba(15,23,42,.035)}
        .dzm-sumcell{display:flex;align-items:center;gap:.55rem;padding:.42rem .74rem}
        .dzm-sumcell + .dzm-sumcell{box-shadow:inset 1px 0 0 ${tint(t.ink[4], 0.12)}}
        .dzm-sumchip{width:28px;height:28px;border-radius:8px;display:inline-grid;place-items:center;flex-shrink:0}
        .dzm-sumchip .ico{width:.95rem;height:.95rem}
        .dzm-sumcell .l{font-size:.6rem;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:${t.ink[3]}}
        .dzm-sumcell .m{font-size:.88rem;font-weight:700;color:${t.ink[0]};display:flex;align-items:center;gap:.36rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
        .dzm-sumcell .s{font-size:.6rem;color:${t.ink[3]}}
        .dzm-sumbadge{font-size:.6rem;font-weight:600;padding:.06rem .4rem;border-radius:999px;flex-shrink:0}
        @media (max-width:960px){.dzm-main{grid-template-columns:1fr}.dzm-summary{grid-template-columns:1fr}.dzm-sumcell + .dzm-sumcell{box-shadow:inset 0 1px 0 ${tint(t.ink[4], 0.12)}}}
      </style>
      <div class="dzm">
        <div class="dzm-head">
          <div class="dzm-sub">Explore health burden and system readiness across ${COUNTRY.name ? `${esc(COUNTRY.name)}'s ` : ''}${esc(levelName('admin1', true).toLowerCase())}${focusName ? `, focused on <b>${esc(focusName)}</b>` : ''}.</div>
          <div class="seg dzm-seg">
            <button data-metric="burden" class="active"><svg class="ico ico-sm"><use href="#i-chart-bar"/></svg>Burden</button>
            <button data-metric="readiness"><svg class="ico ico-sm"><use href="#i-shield-check"/></svg>Readiness</button>
            <button data-metric="reliability"><svg class="ico ico-sm"><use href="#i-check-circle"/></svg>Reliability</button>
          </div>
        </div>
        <div class="dzm-main">
          <div class="dzm-mapwrap">
            <div class="dz-map-mount"></div>
            <div class="dzm-maplegend" aria-hidden="true">
              <span class="dzm-maplegend-cap">Burden</span>
              <div class="dzm-maplegend-scale">
                <span class="dzm-maplegend-hi">High</span>
                <div class="dzm-maplegend-bar"></div>
                <span class="dzm-maplegend-lo">Low</span>
              </div>
            </div>
          </div>
          <div class="dzm-side">
            <div class="dzm-card dzm-sel">
              <div class="dzm-eyebrow">Selected region</div>
              <div class="dzm-sel-top">
                <span class="dzm-sel-name"><svg class="ico ico-sm"><use href="#i-pin"/></svg><span class="dzm-sel-nm">n/a</span></span>
                <span class="dzm-badge dzm-sel-badge"></span>
              </div>
              <div class="dzm-stats">
                <div class="dzm-stat"><div class="k">P(alarm)</div><div class="v dzm-s-alarm">n/a</div></div>
                <div class="dzm-stat"><div class="k">Cases 7d</div><div class="v dzm-s-cases">n/a</div></div>
                <div class="dzm-stat"><div class="k">CFR</div><div class="v dzm-s-cfr">n/a</div></div>
              </div>
              <div class="dzm-spark"><div class="dzm-spark-svg"></div><div class="dzm-spark-cap"></div></div>
            </div>
            <div class="dzm-card dzm-top5">
              <div class="dzm-top5-hd"><span class="dzm-top5-lbl">Top 5</span><span class="u dzm-top5-unit"></span></div>
              <div class="dzm-top5-list"></div>
            </div>
          </div>
        </div>
        <div class="dzm-summary">
          <div class="dzm-sumcell dzm-sum-high"></div>
          <div class="dzm-sumcell dzm-sum-median"></div>
          <div class="dzm-sumcell dzm-sum-fresh"></div>
        </div>
      </div>`;

    const q = <T extends HTMLElement>(sel: string) => ctx.el.querySelector(sel) as T;
    const mapMount = q<HTMLElement>('.dz-map-mount');

    // Selected-region card
    const updateSelected = () => {
      const nm = q<HTMLElement>('.dzm-sel-nm');
      const badge = q<HTMLElement>('.dzm-sel-badge');
      if (!selectedName) { nm.textContent = 'n/a'; badge.style.display = 'none'; return; }
      nm.textContent = selectedName;
      const r = rowsByName[selectedName];
      const m = META[metric];
      const vals = rows.map(x => Number(x[m.col] ?? 0));
      const max = Math.max(1, ...vals);
      const v = Number(r?.[m.col] ?? 0);
      const f = Math.max(0, Math.min(1, v / max));
      // bands[] is ordered low to high for every metric, so the fraction of the max
      // maps directly to a band; only the label meaning differs.
      const bandIdx = Math.min(m.bands.length - 1, Math.floor(f * m.bands.length));
      const badgeCol = bandTone(m, bandIdx);
      badge.style.display = '';
      badge.style.background = tint(badgeCol, 0.16);
      badge.style.color = badgeCol;
      badge.textContent = `${m.bands[bandIdx]} ${m.label.toLowerCase()}`;
      q<HTMLElement>('.dzm-s-alarm').textContent = `${Math.round(Number(r?.p_alarm ?? 0) * (Number(r?.p_alarm ?? 0) <= 1 ? 100 : 1))}%`;
      q<HTMLElement>('.dzm-s-cases').textContent = Number(r?.cases_7d ?? 0).toLocaleString();
      q<HTMLElement>('.dzm-s-cfr').textContent = `${Number(r?.cfr_pct ?? 0).toFixed(1)}%`;
    };

    // Weekly-case sparkline (series via ?spark=1)
    const loadSpark = async (name: string | null) => {
      if (!name || name === lastSparkRegion) return;
      lastSparkRegion = name;
      const svgHost = q<HTMLElement>('.dzm-spark-svg');
      const cap = q<HTMLElement>('.dzm-spark-cap');
      const id = idByName[name];
      if (!id) { svgHost.innerHTML = ''; cap.textContent = ''; return; }
      const series = await ctx.fetchData({ geography: id, spark: '1' });
      if (name !== lastSparkRegion) return;                 // a newer selection won the race
      const pts = (series || []).slice(-10);
      const nums = pts.map((p: any) => Number(p.cases ?? 0));
      if (nums.length < 2) { svgHost.innerHTML = ''; cap.innerHTML = `<span style="color:${t.ink[4]}">Weekly series unavailable</span>`; return; }
      svgHost.innerHTML = sparklineSvg(nums, t, { color: t.status.alert, w: 300, h: 26 });
      const peak = Math.max(...nums);
      cap.innerHTML = `<span>${esc(fmtDate((pts[0] as any).week, true))} to ${esc(fmtDate((pts[pts.length - 1] as any).week, true))}</span><span>peak ${peak.toLocaleString()} cases/wk</span>`;
    };

    const selectRegion = (name: string) => {
      selectedName = name;
      updateSelected();
      // highlight the matching ranked row (regions view only; woreda rows carry no region id)
      ctx.el.querySelectorAll<HTMLElement>('.dzm-row').forEach(el => el.classList.toggle('sel', el.dataset.region === name));
      loadSpark(name);
    };

    // Metric-dependent parts: map, ranked list, legend, summary
    const renderMetricParts = () => {
      const m = META[metric];
      const stops = stopsFor(metric);
      const valuesByRegion: Record<string, number> = {};
      for (const r of rows) valuesByRegion[regionName(String(r.region))] = Number(r[m.col] ?? 0);
      const vals = rows.map(r => Number(r[m.col] ?? 0));
      const max = Math.max(1, ...vals);
      const single = !!soleName;
      const useWoreda = single && woredaRows.length > 0;
      // In single-region scope the map zooms to that region even for a role-cap
      // (focusName may be null but soleName is set).
      const mapFocus = focusName || (single ? soleName : undefined) || undefined;

      // Map: always national geometry; zoom and focus when a single region is in scope.
      if (chartCleanup) { chartCleanup(); chartCleanup = undefined; }
      mapMount.innerHTML = '';   // choropleth cleanup leaves its host div; clear it so metric toggles don't stack maps
      const ramp = metric === 'burden' ? stops : undefined;   // undefined: choropleth's amber ramp (matches `stops`)
      const tooltip = (name: string) => {
        const r = rowsByName[name];
        if (!r) return `<b>${esc(name)}</b><br/>No data`;
        const pa = Number(r.p_alarm ?? 0); const paPct = Math.round(pa <= 1 ? pa * 100 : pa);
        return `<b>${esc(name)}</b><br/>Burden ${Math.round(Number(r.burden_incidence_100k ?? 0))}/100k, Readiness ${Math.round(Number(r.readiness_pct ?? 0))}%<br/>P(alarm) ${paPct}%, Cases 7d ${Number(r.cases_7d ?? 0).toLocaleString()}`;
      };
      chartCleanup = choropleth(mapMount, {
        valuesByRegion, fmt: m.fmt, onSelect: selectRegion, ramp, tooltip,
        showScale: false,                                     // we draw our own overlaid legend
        worldContext: true,                                   // real surrounding land on water
        height: 300,
        focusRegion: mapFocus,
        selected: selectedName || mapFocus,
      }, t, geo);

      // Overlaid vertical legend: gradient from bottom (low) to top (high), labelled by metric.
      q<HTMLElement>('.dzm-maplegend-cap').textContent = m.label;
      q<HTMLElement>('.dzm-maplegend-bar').style.background = `linear-gradient(to top, ${stops.join(',')})`;

      // Ranked list: national top 5 regions or, in a single-region scope, that
      // region's woreda burden hotspots so the list never collapses to one bar.
      const listHost = q<HTMLElement>('.dzm-top5-list');
      if (useWoreda) {
        const wRanked = [...woredaRows]
          .filter(w => w.burden_incidence_100k != null)
          .sort((a, b) => Number(b.burden_incidence_100k ?? 0) - Number(a.burden_incidence_100k ?? 0))
          .slice(0, 5);
        const wMax = Math.max(1, ...wRanked.map(w => Number(w.burden_incidence_100k ?? 0)));
        q<HTMLElement>('.dzm-top5-lbl').textContent = `Burden hotspots: ${soleName}`;
        q<HTMLElement>('.dzm-top5-unit').textContent = 'cases/100k';
        listHost.innerHTML = wRanked.map(w => {
          const name = String(w.woreda_name ?? w.region ?? 'n/a');
          const v = Number(w.burden_incidence_100k ?? 0);
          const wd = Math.max(4, Math.round((v / wMax) * 100));
          const col = colorAt(BURDEN_RAMP, Math.max(0, Math.min(1, v / wMax)));
          return `<div class="dzm-row" data-woreda="${esc(name)}">
            <span class="n" title="${esc(name)}">${esc(name)}</span>
            <span class="track"><span style="width:${wd}%;background:${col}"></span></span>
            <span class="val">${Math.round(v)}</span></div>`;
        }).join('');
        // woreda rows are informational (woredas are not map-selectable): no click handler
      } else {
        // Top 5 by metric; higher-is-better metrics rank ascending so the weakest
        // regions surface, the same attention framing as burden.
        const ranked = [...rows].sort((a, b) => {
          const av = Number(a[m.col] ?? 0), bv = Number(b[m.col] ?? 0);
          return m.higherWorse ? bv - av : av - bv;
        }).slice(0, 5);
        const listMax = Math.max(1, ...ranked.map(r => Number(r[m.col] ?? 0)));
        q<HTMLElement>('.dzm-top5-lbl').textContent = m.higherWorse ? `Top 5 ${m.label.toLowerCase()} regions` : `Lowest 5 ${m.label.toLowerCase()} regions`;
        q<HTMLElement>('.dzm-top5-unit').textContent = metric === 'burden' ? 'cases/100k' : '%';
        listHost.innerHTML = ranked.map(r => {
          const name = regionName(String(r.region));
          const v = Number(r[m.col] ?? 0);
          const w = Math.max(4, Math.round((v / listMax) * 100));
          const col = colorAt(stops, Math.max(0, Math.min(1, v / max)));
          return `<div class="dzm-row${name === selectedName ? ' sel' : ''}" data-region="${esc(name)}">
            <span class="n" title="${esc(name)}">${esc(name)}</span>
            <span class="track"><span style="width:${w}%;background:${col}"></span></span>
            <span class="val">${esc(m.fmt(v))}</span></div>`;
        }).join('');
        ctx.el.querySelectorAll<HTMLElement>('.dzm-row[data-region]').forEach(el => {
          el.style.cursor = 'pointer';
          el.onclick = () => { const n = el.dataset.region!; if (rowsByName[n]) selectRegion(n); };
        });
      }

      // Summary strip: in a single-region scope it reports woreda statistics for
      // that region instead of a one-region national median.
      if (useWoreda) {
        const wVals = woredaRows.map(w => Number(w.burden_incidence_100k ?? 0)).filter(v => v > 0);
        const topW = [...woredaRows].sort((a, b) => Number(b.burden_incidence_100k ?? 0) - Number(a.burden_incidence_100k ?? 0))[0];
        const topWName = topW ? String(topW.woreda_name ?? 'n/a') : 'n/a';
        const topWVal = topW ? Number(topW.burden_incidence_100k ?? 0) : 0;
        const topWCol = colorAt(BURDEN_RAMP, Math.max(0, Math.min(1, topWVal / Math.max(1, topWVal))));
        const wMed = median(wVals);
        q<HTMLElement>('.dzm-sum-high').innerHTML =
          `<span class="dzm-sumchip" style="background:${tint(t.status.alert, 0.13)};color:${t.status.alert}"><svg class="ico"><use href="#i-star"/></svg></span>
           <span style="min-width:0"><span class="l">Highest-burden woreda</span>
           <span class="m" title="${esc(topWName)}">${esc(topWName)}<span class="dzm-sumbadge" style="background:${tint(topWCol, 0.16)};color:${topWCol}">${Math.round(topWVal)}</span></span></span>`;
        q<HTMLElement>('.dzm-sum-median').innerHTML =
          `<span class="dzm-sumchip" style="background:${tint(t.navy['600'], 0.12)};color:${t.navy['600']}"><svg class="ico"><use href="#i-chart-bar"/></svg></span>
           <span style="min-width:0"><span class="l">${esc(soleName!)} median burden</span><span class="m">${Math.round(wMed)}<span style="font-size:.6rem;font-weight:500;color:${t.ink[3]}">cases/100k</span></span>
           <span class="s">across ${wVals.length} woreda${wVals.length === 1 ? '' : 's'}</span></span>`;
      } else {
        const sorted = [...rows].sort((a, b) => Number(b[m.col] ?? 0) - Number(a[m.col] ?? 0));
        const highest = sorted[0];
        const highName = highest ? regionName(String(highest.region)) : 'n/a';
        const highVal = highest ? Number(highest[m.col] ?? 0) : 0;
        const highCol = colorAt(stops, Math.max(0, Math.min(1, highVal / max)));
        const med = median(vals);
        q<HTMLElement>('.dzm-sum-high').innerHTML =
          `<span class="dzm-sumchip" style="background:${tint(t.status.alert, 0.13)};color:${t.status.alert}"><svg class="ico"><use href="#i-star"/></svg></span>
           <span style="min-width:0"><span class="l">Highest ${esc(m.label.toLowerCase())} region</span>
           <span class="m" title="${esc(highName)}">${esc(highName)}<span class="dzm-sumbadge" style="background:${tint(highCol, 0.16)};color:${highCol}">${esc(m.fmt(highVal))}</span></span></span>`;
        q<HTMLElement>('.dzm-sum-median').innerHTML =
          `<span class="dzm-sumchip" style="background:${tint(t.navy['600'], 0.12)};color:${t.navy['600']}"><svg class="ico"><use href="#i-chart-bar"/></svg></span>
           <span style="min-width:0"><span class="l">National median</span><span class="m">${esc(m.fmt(med))}</span>
           <span class="s">across ${rows.length} region${rows.length === 1 ? '' : 's'} in scope</span></span>`;
      }
      q<HTMLElement>('.dzm-sum-fresh').innerHTML =
        `<span class="dzm-sumchip" style="background:${tint(t.navy['500'], 0.12)};color:${t.navy['500']}"><svg class="ico"><use href="#i-clock"/></svg></span>
         <span style="min-width:0"><span class="l">Data freshness</span><span class="m">${dataThrough ? esc(fmtDate(dataThrough)) : 'n/a'}</span>
         <span class="s">latest reported week</span></span>`;

      updateSelected();
    };

    const boot = async () => {
      mapMount.innerHTML = '<p class="muted" style="padding:1rem">Loading…</p>';
      try {
        // Always national (OPA still row-filters to the caller's scope). This keeps
        // the national context, top 5 and summary intact; the global filter only
        // drives the map zoom and selection.
        rows = (await ctx.fetchData({ geography: 'all' })) || [];
        if (!rows.length) {
          mapMount.innerHTML = '<div class="widget-empty">No geographic surveillance data for the selected filters.</div>';
          return;
        }
        rowsByName = {}; idByName = {};
        for (const r of rows) {
          const name = regionName(String(r.region));
          rowsByName[name] = r; idByName[name] = String(r.region);
        }
        dataThrough = rows[0]?.data_through ?? null;

        // Single region in scope? A picked region (focusName), or an OPA role cap
        // that collapsed the national fetch to one region. Then drill to woredas.
        if (!soleName && rows.length === 1) {
          soleId = String(rows[0].region);
          soleName = regionName(soleId);
        }
        if (soleName && soleId) {
          woredaRows = (await ctx.fetchData({ geography: soleId, woreda: '1' })) || [];
        }

        geo = await loadRegionContextGeo();       // the profile's regions + faint surrounding land
        mapMount.innerHTML = '';

        // Initial selection: the single/globally-focused region, else top-burden.
        const topBurden = [...rows].sort((a, b) => Number(b.burden_incidence_100k ?? 0) - Number(a.burden_incidence_100k ?? 0))[0];
        selectedName = soleName || (topBurden ? regionName(String(topBurden.region)) : null);

        renderMetricParts();                // draws the map (fires onSelect to sidebar + spark)
        if (selectedName) selectRegion(selectedName);
      } catch {
        mapMount.innerHTML = '<div class="widget-empty">Could not load the burden &amp; readiness map. Please try again.</div>';
      }
    };

    ctx.el.querySelectorAll<HTMLElement>('[data-metric]').forEach(b => {
      b.onclick = () => {
        metric = b.dataset.metric as Metric;
        ctx.el.querySelectorAll<HTMLElement>('[data-metric]').forEach(x => x.classList.toggle('active', x.dataset.metric === metric));
        if (rows.length && geo) renderMetricParts();          // no refetch: all metrics are in `rows`
      };
    });

    boot();
    return () => { if (chartCleanup) chartCleanup(); };
  },
};
export default def;
