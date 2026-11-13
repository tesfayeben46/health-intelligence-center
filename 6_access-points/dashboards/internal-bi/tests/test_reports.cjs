/**
 * Report generation: the Export page's ex-templates widget against the
 * /gw/reports backend (list templates, generate, presigned download).
 * Verifies the widget lists the seeded templates and a Generate click produces
 * an OPA-gated, scope-enforced report (success status + row count).
 *
 * Run: NODE_PATH=<playwright-install>/node_modules node tests/test_reports.cjs
 */
const { chromium } = require('playwright');
const BI = 'https://internal-bi.127.0.0.1.nip.io/';
const PW = process.env.SEED_PASSWORD;
const failures = [];
const check = (c, l) => { console.log((c ? '  ok   ' : '  FAIL ') + l); if (!c) failures.push(l); };

async function login(pg, user) {
  await pg.goto(BI, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await pg.waitForSelector('#username', { timeout: 30000 });
  await pg.fill('#username', user); await pg.fill('#password', PW);
  await pg.click('#kc-login');
  await pg.waitForFunction(() => !document.querySelector('#username') && !!window.__nhdwAccessToken, { timeout: 60000 });
  await pg.waitForTimeout(2000);
}

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1500, height: 1200 }, acceptDownloads: true });
  const pg = await ctx.newPage();
  await login(pg, 'dec.nat');   // R-DEC (national): can export

  // Navigate to the Export page + wait for the widget to populate (poll the DOM
  // directly: the widget container isn't a Playwright "visible" target).
  await pg.evaluate(() => window.__nhdwDashboard.navigate_to_page({ page_id: 'internal-bi/export' }));
  await pg.waitForFunction(() => {
    const el = document.querySelector('[data-widget-id="ex-templates"]');
    return el && el.querySelectorAll('button[data-act="gen"]').length >= 3;
  }, { timeout: 25000 }).catch(() => {});

  // 1. The widget lists the seeded report templates + Generate buttons.
  const info = await pg.evaluate(() => {
    const el = document.querySelector('[data-widget-id="ex-templates"]');
    const text = el ? el.innerText : '';
    const gens = el ? el.querySelectorAll('button[data-act="gen"]').length : 0;
    return { text, gens };
  });
  check(info.gens >= 3, `ex-templates lists report templates with Generate buttons (${info.gens})`);
  check(/Disease surveillance/i.test(info.text), `a real template is shown (Disease surveillance)`);
  check(/mart_/i.test(info.text), `templates show their backing dataset (mart)`);

  // 2. Generate one: a real report is produced (success status + row count).
  await pg.evaluate(() => {
    const el = document.querySelector('[data-widget-id="ex-templates"]');
    el.querySelector('button[data-act="gen"]').click();
  });
  await pg.waitForFunction(() => {
    const s = document.querySelector('[data-widget-id="ex-templates"] [data-status]');
    return s && /ready|Failed|error/i.test(s.innerText);
  }, { timeout: 40000 }).catch(() => {});
  const status = await pg.evaluate(() => {
    const s = document.querySelector('[data-widget-id="ex-templates"] [data-status]');
    return s ? s.innerText : '';
  });
  check(/Report ready/.test(status) && /rows/i.test(status), `Generate produced a real report (status: "${status.trim().slice(0, 80)}")`);

  await pg.close();
  await browser.close();
  if (failures.length) { console.log(`\nFAILED: ${failures.length}`); process.exit(1); }
  console.log('\nALL GREEN'); process.exit(0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
