/**
 * Assistant to spec flip: the chatbot's SPA-executor tools (window.__nhdwDashboard,
 * driven server-side by the gateway's ops_perm-resolved chat) must be able to SEE
 * catalog-runtime widgets. Before the flip, read_page returned [] on catalog pages
 * because PageRenderer cards use `data-widget-id`, not element `id`.
 *
 * Verifies on the Resource page: navigate_to_page, then read_page lists the rm-*
 * widgets with titles, then read_widget returns a text preview.
 *
 * Run: NODE_PATH=<playwright-install>/node_modules node tests/test_assistant_tools.cjs
 */
const { chromium } = require('playwright');
const BI = 'https://internal-bi.127.0.0.1.nip.io/';
const PW = process.env.SEED_PASSWORD;
const failures = [];
const check = (c, l) => { console.log((c ? '  ok   ' : '  FAIL ') + l); if (!c) failures.push(l); };

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1500, height: 1200 } });
  const pg = await ctx.newPage();
  await pg.goto(BI, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await pg.waitForSelector('#username', { timeout: 30000 });
  await pg.fill('#username', 'tadesse.h'); await pg.fill('#password', PW);
  await pg.click('#kc-login');
  await pg.waitForFunction(() => document.querySelectorAll('.bi-nav a').length > 0 && !document.querySelector('#username'), { timeout: 60000 });
  await pg.waitForTimeout(2500);

  // Drive the assistant's own tools (the ones the gateway calls): navigate,
  // then read the page + a widget. This is exactly what the chatbot executes.
  const out = await pg.evaluate(async () => {
    const api = window.__nhdwDashboard;
    if (!api) return { err: 'no __nhdwDashboard' };
    api.navigate_to_page({ page_id: 'internal-bi/resource' });
    await new Promise(r => setTimeout(r, 6000));   // catalog render + fetches
    const page = api.read_page();
    const heroTitle = (page.widgets.find(w => w.id === 'rm-hero') || {}).title;
    const w = api.read_widget({ widget_id: 'rm-hero' });
    const wPipe = api.read_widget({ widget_id: 'rm-hotspots' });
    return {
      page_id: page.page_id,
      widget_ids: page.widgets.map(x => x.id),
      titled: page.widgets.filter(x => x.title && x.title !== x.id).length,
      heroTitle,
      heroPreview: (w && w.text_preview || '').slice(0, 60),
      pipeOk: !!(wPipe && wPipe.ok && wPipe.text_preview),
    };
  });

  if (out.err) { console.log('  FAIL ' + out.err); failures.push(out.err); }
  else {
    check(out.page_id === 'internal-bi/resource', `navigate + read_page on Resource (page_id=${out.page_id})`);
    const rm = out.widget_ids.filter(id => id.startsWith('rm-'));
    check(rm.length >= 10, `read_page SEES catalog rm-* widgets (${rm.length} found: ${rm.slice(0,4).join(',')}…)`);
    check(out.titled >= 12, `widgets carry real titles (${out.titled} titled)`);
    check(!!out.heroTitle && out.heroTitle !== 'rm-hero', `rm-hero has a human title ("${out.heroTitle}")`);
    check(out.heroPreview.length > 0, `read_widget(rm-hero) returns a text preview ("${out.heroPreview}…")`);
    check(out.pipeOk, `read_widget(rm-hotspots) ok with preview`);
  }

  await browser.close();
  if (failures.length) { console.log(`\nFAILED: ${failures.length}`); process.exit(1); }
  console.log('\nALL GREEN'); process.exit(0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
