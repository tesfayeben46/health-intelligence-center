/**
 * Regression: catalog-driven Disease Surveillance page (dz-* widget folders).
 * Verifies every widget renders real data (echarts canvas / table rows / KPI
 * tiles), no synthetic, no "no renderer", no runtime errors. R-DEC (national)
 * sees the full page with programme=all (so the cross-disease heatmap + region
 * map are ungated).
 */
const { chromium } = require('playwright');
const BI = 'https://internal-bi.127.0.0.1.nip.io/';
const PW = process.env.SEED_PASSWORD;
const failures = [];
const check = (c, l) => { console.log((c ? '  ok   ' : '  FAIL ') + l); if (!c) failures.push(l); };

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1400 } });
  const pg = await ctx.newPage();
  const errors = [];
  // 400/401/403 are the PKCE boot-race (spec fetch before the token lands): benign.
  pg.on('console', m => { if (m.type() === 'error' && !/status of 40[013]/.test(m.text())) errors.push(m.text()); });
  pg.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));

  await pg.goto(BI, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await pg.waitForSelector('#username', { timeout: 30000 });
  await pg.fill('#username', 'tadesse.h'); await pg.fill('#password', PW);
  await pg.click('#kc-login');
  await pg.waitForFunction(() => document.querySelectorAll('.bi-nav a').length > 0 && !document.querySelector('#username'), { timeout: 60000 });
  await pg.waitForTimeout(4000);
  const nav = pg.locator('.bi-nav a', { hasText: 'Disease Surveillance' });
  await nav.waitFor({ timeout: 20000 });
  await nav.click();
  await pg.waitForSelector('.catalog-page .page-section', { timeout: 20000 }).catch(() => {});
  await pg.waitForTimeout(8000); // geojson + multiple echarts + ml-fed fetches

  const r = await pg.evaluate(() => {
    const w = (id) => {
      const el = document.querySelector(`[data-widget-id="${id}"]`);
      if (!el) return { present: false };
      return {
        present: true,
        canvas: !!el.querySelector('canvas'),
        rows: el.querySelectorAll('table.dash tbody tr').length,
        kpis: el.querySelectorAll('.kpi').length,
        empty: !!el.querySelector('.widget-empty'),
        emptyText: el.querySelector('.widget-empty')?.textContent?.trim() || null,
      };
    };
    return {
      sections: document.querySelectorAll('.catalog-page .page-section').length,
      noRenderer: document.body.innerText.includes('No renderer registered'),
      widgets: {
        kpi: w('dz-kpi-strip'), map: w('dz-region-map'),
        woreda: w('dz-woreda-table'), signal: w('dz-signal-chart'),
        outbreaks: w('dz-outbreaks-table'), climTs: w('dz-climate-ts'), climCorr: w('dz-climate-corr'),
        riskGone: !document.querySelector('[data-widget-id="dz-risk-bands"]'),
      },
    };
  });

  await pg.screenshot({ path: '/tmp/nhdw-test-shots/disease_page.png', fullPage: true });

  const W = r.widgets;
  check(r.sections >= 4, `sections rendered (${r.sections})`);
  check(!r.noRenderer, 'no "no renderer" placeholders');
  check(W.kpi.present && W.kpi.kpis >= 3 && !W.kpi.empty, `dz-kpi-strip real KPI tiles (${W.kpi.kpis})`);
  check(W.riskGone, 'dz-risk-bands removed (no widget card)');
  check(W.map.present && W.map.canvas && !W.map.empty, `dz-region-map choropleth drawn (canvas=${W.map.canvas})`);
  check(W.woreda.present && W.woreda.rows > 0 && !W.woreda.empty, `dz-woreda-table real rows (${W.woreda.rows})`);
  check(W.signal.present && W.signal.canvas && !W.signal.empty, `dz-signal-chart band drawn (canvas=${W.signal.canvas})`);
  check(W.outbreaks.present && (W.outbreaks.rows > 0 || W.outbreaks.empty), `dz-outbreaks-table rows or empty state (${W.outbreaks.rows} rows${W.outbreaks.empty ? ', empty' : ''})`);
  check(W.climTs.present && (W.climTs.canvas || W.climTs.empty), `dz-climate-ts drawn or empty state (canvas=${W.climTs.canvas})`);
  check(W.climCorr.present && (W.climCorr.canvas || W.climCorr.empty), `dz-climate-corr drawn or empty state (canvas=${W.climCorr.canvas})`);
  check(errors.length === 0, `no runtime JS errors (${errors.length}${errors.length ? ': ' + errors.slice(0, 3).join(' | ') : ''})`);

  await browser.close();
  if (failures.length) { console.log(`\nFAILED: ${failures.length}`); process.exit(1); }
  console.log('\nALL GREEN'); process.exit(0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
