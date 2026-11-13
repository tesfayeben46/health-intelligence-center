/**
 * Regression: catalog-driven Overview page (digest). A general national band +
 * one band per other page (Disease/Resource/Service), each digesting it in a
 * couple of widgets, with a "View full page" jump. All real data (6 digests
 * reuse sibling fetchers; ov-national-kpis is a cross-mart national query).
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
  pg.on('console', m => { if (m.type() === 'error' && !/status of 40[013]/.test(m.text())) errors.push(m.text()); });
  pg.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));

  await pg.goto(BI, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await pg.waitForSelector('#username', { timeout: 30000 });
  await pg.fill('#username', 'tadesse.h'); await pg.fill('#password', PW);
  await pg.click('#kc-login');
  await pg.waitForFunction(() => document.querySelectorAll('.bi-nav a').length > 0 && !document.querySelector('#username'), { timeout: 60000 });
  await pg.waitForTimeout(4000);
  const nav = pg.locator('.bi-nav a', { hasText: 'Overview' });
  await nav.waitFor({ timeout: 20000 });
  await nav.click();
  await pg.waitForSelector('.catalog-page .page-section', { timeout: 20000 }).catch(() => {});
  await pg.waitForTimeout(6000);

  const r = await pg.evaluate(() => {
    const w = (id) => {
      const el = document.querySelector(`[data-widget-id="${id}"]`);
      if (!el) return { present: false };
      return { present: true, canvas: !!el.querySelector('canvas'), rows: el.querySelectorAll('table.dash tbody tr').length, kpis: el.querySelectorAll('.kpi').length, empty: !!el.querySelector('.widget-empty') };
    };
    return {
      sectionTitles: [...document.querySelectorAll('.catalog-page .page-section .ps-title')].map(e => e.textContent.trim()),
      fullPageChips: document.querySelectorAll('.catalog-page .ps-fullpage').length,
      noRenderer: document.body.innerText.includes('No renderer registered'),
      widgets: {
        nat: w('ov-national-kpis'), dp: w('ov-disease-pulse'), dob: w('ov-disease-outbreaks'),
        rs: w('ov-resource-status'), rf: w('ov-resource-facilities'), sa: w('ov-service-attainment'), sc: w('ov-service-cascade'),
      },
    };
  });

  await pg.screenshot({ path: '/tmp/nhdw-test-shots/overview_page.png', fullPage: true });

  const W = r.widgets;
  check(r.sectionTitles.length === 4, `4 digest sections (${r.sectionTitles.join(' / ')})`);
  check(r.sectionTitles.includes('Disease Surveillance') && r.sectionTitles.includes('Resource Management') && r.sectionTitles.includes('Service Performance'), 'sections named after the other pages');
  check(r.fullPageChips === 3, `3 "View full page" jumps (${r.fullPageChips})`);
  check(W.nat.present && W.nat.kpis >= 5 && !W.nat.empty, `ov-national-kpis 5 real tiles (${W.nat.kpis})`);
  check(W.dp.present && W.dp.kpis >= 3 && !W.dp.empty, `ov-disease-pulse real tiles (${W.dp.kpis})`);
  check(W.dob.present && W.dob.rows > 0 && !W.dob.empty, `ov-disease-outbreaks real rows (${W.dob.rows})`);
  check(W.rs.present && W.rs.kpis >= 3 && !W.rs.empty, `ov-resource-status real tiles (${W.rs.kpis})`);
  check(W.rf.present && W.rf.rows > 0 && !W.rf.empty, `ov-resource-facilities real rows (${W.rf.rows})`);
  check(W.sa.present && W.sa.kpis >= 3 && !W.sa.empty, `ov-service-attainment real tiles (${W.sa.kpis})`);
  check(W.sc.present && W.sc.canvas && !W.sc.empty, `ov-service-cascade chart drawn (canvas=${W.sc.canvas})`);
  check(!r.noRenderer, 'no "no renderer" placeholders');
  check(errors.length === 0, `no runtime JS errors (${errors.length}${errors.length ? ': ' + errors.slice(0, 2).join(' | ') : ''})`);

  await browser.close();
  if (failures.length) { console.log(`\nFAILED: ${failures.length}`); process.exit(1); }
  console.log('\nALL GREEN'); process.exit(0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
