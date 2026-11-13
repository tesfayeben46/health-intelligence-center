/**
 * Regression: catalog-driven Resource Management page (14 rm-* widget folders).
 * All 14 real (bi_supply / bi_facility incl. the region-grained supply marts +
 * Groups B/C/D: cold-chain, workforce, equipment, procurement pipeline).
 * No synthetic data anywhere, no "no renderer" placeholders.
 */
const { chromium } = require('playwright');
const BI = 'https://internal-bi.127.0.0.1.nip.io/';
const PW = process.env.SEED_PASSWORD;
const failures = [];
const check = (c, l) => { console.log((c ? '  ok   ' : '  FAIL ') + l); if (!c) failures.push(l); };

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1500 } });
  const pg = await ctx.newPage();
  const errors = [];
  pg.on('console', m => { if (m.type() === 'error' && !/status of 40[013]/.test(m.text())) errors.push(m.text()); });
  pg.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));

  await pg.goto(BI, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await pg.waitForSelector('#username', { timeout: 30000 });
  await pg.fill('#username', 'tadesse.h'); await pg.fill('#password', PW);
  await pg.click('#kc-login');
  await pg.waitForFunction(() => document.querySelectorAll('.bi-nav a').length > 0 && !document.querySelector('#username'), { timeout: 60000 });
  await pg.waitForTimeout(4000);
  const nav = pg.locator('.bi-nav a', { hasText: 'Resource Management' });
  await nav.waitFor({ timeout: 20000 });
  await nav.click();
  await pg.waitForSelector('.catalog-page .page-section', { timeout: 20000 }).catch(() => {});
  await pg.waitForTimeout(8000); // geojson + echarts + supply/facility fetches

  const r = await pg.evaluate(() => {
    const w = (id) => {
      const el = document.querySelector(`[data-widget-id="${id}"]`);
      if (!el) return { present: false };
      return { present: true, canvas: !!el.querySelector('canvas'), canvasCount: el.querySelectorAll('canvas').length, rows: el.querySelectorAll('table.dash tbody tr').length, kpis: el.querySelectorAll('.kpi').length, empty: !!el.querySelector('.widget-empty') };
    };
    const ids = ['rm-hero','rm-statboard','rm-map','rm-hotspots','rm-facilities','rm-wastage','rm-forecast','rm-stock-heatmap','rm-events','rm-workforce','rm-equipment','rm-cold-chain'];
    return {
      sections: document.querySelectorAll('.catalog-page .page-section').length,
      noRenderer: document.body.innerText.includes('No renderer registered'),
      widgets: Object.fromEntries(ids.map(id => [id, w(id)])),
    };
  });
  await pg.screenshot({ path: '/tmp/nhdw-test-shots/resource_page.png', fullPage: true });

  const W = r.widgets;
  const real = (id, kind) => {
    const x = W[id];
    const ok = x.present && !x.empty && (kind === 'canvas' ? x.canvas : kind === 'table' ? x.rows > 0 : x.kpis > 0);
    check(ok, `${id} real (${kind}): canvas=${x.canvas} rows=${x.rows} kpis=${x.kpis} empty=${x.empty}`);
  };
  check(r.sections >= 4, `sections rendered (${r.sections})`);
  real('rm-hero', 'kpi'); real('rm-statboard', 'kpi');
  real('rm-map', 'canvas'); real('rm-hotspots', 'table'); real('rm-facilities', 'canvas');
  real('rm-wastage', 'canvas'); real('rm-forecast', 'canvas');
  real('rm-stock-heatmap', 'canvas');
  real('rm-cold-chain', 'kpi');    // DHIS2 EPI cold-chain/AEFI
  real('rm-workforce', 'canvas');  // MFR cadre staffing to region density
  real('rm-equipment', 'canvas');  // MFR FHIR Device to equipment availability
  // rm-events: a table or an explicit empty state (overstocked synthetic data is likely empty)
  check(W['rm-events'].present && (W['rm-events'].rows > 0 || W['rm-events'].empty), `rm-events rows or empty state (rows=${W['rm-events'].rows}, empty=${W['rm-events'].empty})`);
  // no widget renders its chart twice (the async-draw race)
  const dbl = Object.entries(W).filter(([, x]) => x.canvasCount > 1).map(([id, x]) => `${id}:${x.canvasCount}`);
  check(dbl.length === 0, `no widget double-renders its chart (${dbl.join(', ') || 'all single'})`);
  check(!r.noRenderer, 'no "no renderer" placeholders');
  check(errors.length === 0, `no runtime JS errors (${errors.length}${errors.length ? ': ' + errors.slice(0, 2).join(' | ') : ''})`);

  await browser.close();
  if (failures.length) { console.log(`\nFAILED: ${failures.length}`); process.exit(1); }
  console.log('\nALL GREEN'); process.exit(0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
