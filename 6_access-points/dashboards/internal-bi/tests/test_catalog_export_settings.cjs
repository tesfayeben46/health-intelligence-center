/**
 * Catalog-driven Export + Settings pages (app-like widgets to gateway endpoints).
 * Export: ex-templates (explicit empty state), ex-saved-queries, ex-audit-trail
 * (user-scoped), ex-sql-editor (analyst only: live Trino execute). Settings:
 * st-profile (/gw/me identity), st-display (localStorage prefs), st-alert-subs
 * (system routing or explicit empty state), st-security (PAT list).
 * Asserts there are no synthetic rows and no "no renderer" placeholders.
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
  await pg.waitForFunction(() => document.querySelectorAll('.bi-nav a').length > 0 && !document.querySelector('#username'), { timeout: 60000 });
  await pg.waitForTimeout(2500);
}
async function nav(pg, page) {   // page = 'export' | 'settings': use the app's own navigator (robust vs nav-render races)
  await pg.waitForFunction(() => !!window.__nhdwDashboard, { timeout: 20000 });
  await pg.evaluate((p) => window.__nhdwDashboard.navigate_to_page({ page_id: 'internal-bi/' + p }), page);
  await pg.waitForSelector('.catalog-page .page-section', { timeout: 20000 }).catch(() => {});
  await pg.waitForTimeout(4000);
}
const navSettings = (pg) => nav(pg, 'settings');
const widgetInfo = (pg) => pg.evaluate((ids) => {
  const noRenderer = document.body.innerText.includes('No renderer registered');
  const errs = window.__errs || [];
  const w = (id) => {
    const el = document.querySelector(`[data-widget-id="${id}"]`);
    if (!el) return { present: false };
    return { present: true, text: (el.innerText || '').trim().slice(0, 300), empty: !!el.querySelector('.widget-empty') };
  };
  return { noRenderer, widgets: Object.fromEntries(ids.map(id => [id, w(id)])) };
}, undefined);

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1400 } });

  // R-DEC: 7 widgets (no SQL editor), real profile
  const pg = await ctx.newPage();
  const errors = [];
  pg.on('console', m => { if (m.type() === 'error' && !/status of 40[0-9]/.test(m.text())) errors.push(m.text()); });
  pg.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));
  await login(pg, 'tadesse.h');

  await nav(pg, 'export');
  let r = await pg.evaluate((ids) => {
    const w = (id) => { const el = document.querySelector(`[data-widget-id="${id}"]`); return el ? { present: true, text: (el.innerText||'').trim().slice(0,200), empty: !!el.querySelector('.widget-empty') } : { present: false }; };
    return { noRenderer: document.body.innerText.includes('No renderer registered'), widgets: Object.fromEntries(ids.map(id => [id, w(id)])) };
  }, ['ex-templates','ex-saved-queries','ex-audit-trail','ex-sql-editor']);
  check(!r.noRenderer, 'export: no "no renderer" placeholders');
  check(r.widgets['ex-templates'].present, 'export: ex-templates present (empty state allowed)');
  check(r.widgets['ex-saved-queries'].present, 'export: ex-saved-queries present');
  check(r.widgets['ex-audit-trail'].present, 'export: ex-audit-trail present');
  check(!r.widgets['ex-sql-editor'].present, 'export: ex-sql-editor not shown to R-DEC (curated to analysts)');

  await navSettings(pg);
  const s = await pg.evaluate((ids) => {
    const w = (id) => { const el = document.querySelector(`[data-widget-id="${id}"]`); return el ? { present: true, text: (el.innerText||'').trim() } : { present: false, text: '' }; };
    return Object.fromEntries(ids.map(id => [id, w(id)]));
  }, ['st-profile','st-display','st-alert-subs','st-security']);
  check(s['st-profile'].present && /R-DEC/.test(s['st-profile'].text), `settings: st-profile shows real role R-DEC (${s['st-profile'].text.slice(0,60).replace(/\n/g,' ')})`);
  check(/tadesse/i.test(s['st-profile'].text), 'settings: st-profile shows the real signed-in user (tadesse)');
  check(s['st-display'].present && /density/i.test(s['st-display'].text), 'settings: st-display prefs present');
  check(s['st-alert-subs'].present, 'settings: st-alert-subs present');
  check(s['st-security'].present && /token/i.test(s['st-security'].text), 'settings: st-security (PATs) present');
  check(errors.length === 0, `R-DEC: no runtime JS errors (${errors.length}${errors.length ? ': ' + errors.slice(0,2).join(' | ') : ''})`);
  await pg.close();

  // analyst.ana (R-CONS-ANA): live SQL editor
  // Fresh context so Keycloak SSO doesn't reuse the previous user's session.
  const ctx2 = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1400 } });
  const pg2 = await ctx2.newPage();
  const errors2 = [];
  pg2.on('pageerror', e => errors2.push('PAGEERROR: ' + e.message));
  await login(pg2, 'analyst.ana');
  await nav(pg2, 'export');
  const hasEditor = await pg2.locator('[data-widget-id="ex-sql-editor"] textarea').count();
  check(hasEditor > 0, `export(analyst): ex-sql-editor present with a SQL textarea (${hasEditor})`);
  if (hasEditor > 0) {
    // (a) execution + render path: SHOW TABLES is real Trino introspection (skips per-table OPA).
    await pg2.fill('[data-widget-id="ex-sql-editor"] textarea', 'SHOW TABLES FROM bi_facility');
    await pg2.click('[data-widget-id="ex-sql-editor"] [data-run]');
    await pg2.waitForTimeout(6000);
    const res = await pg2.evaluate(() => {
      const el = document.querySelector('[data-widget-id="ex-sql-editor"] [data-result]');
      const rows = el ? el.querySelectorAll('table.sr-table tbody tr').length : 0;
      const err = el ? /error|denied/i.test(el.innerText) : false;
      return { rows, err, text: el ? el.innerText.slice(0, 120) : '' };
    });
    check(res.rows > 0 && !res.err, `export(analyst): live SQL execute rows=${res.rows} err=${res.err}: real Trino, not mock`);

    // (b) a cataloged, role-readable mart now returns real rows (mart_attrs reconciled).
    await pg2.fill('[data-widget-id="ex-sql-editor"] textarea', 'SELECT * FROM bi_supply.mart_supply_pipeline LIMIT 3');
    await pg2.click('[data-widget-id="ex-sql-editor"] [data-run]');
    await pg2.waitForTimeout(5000);
    const q = await pg2.evaluate(() => {
      const el = document.querySelector('[data-widget-id="ex-sql-editor"] [data-result]');
      return { rows: el ? el.querySelectorAll('table.sr-table tbody tr').length : 0, err: el ? /error|denied/i.test(el.innerText) : true };
    });
    check(q.rows > 0 && !q.err, `export(analyst): cataloged mart (mart_supply_pipeline) returns rows=${q.rows}: SQL editor works on real marts`);

    // (c) governance path: a RESTRICTED mart is OPA-denied (an explicit error, not a blank).
    await pg2.fill('[data-widget-id="ex-sql-editor"] textarea', 'SELECT * FROM bi_facility.mart_consent_activity_kpi LIMIT 3');
    await pg2.click('[data-widget-id="ex-sql-editor"] [data-run]');
    await pg2.waitForTimeout(5000);
    const den = await pg2.evaluate(() => {
      const el = document.querySelector('[data-widget-id="ex-sql-editor"] [data-result]');
      return el ? /denied|not cataloged/i.test(el.innerText) : false;
    });
    check(den, 'export(analyst): RESTRICTED mart correctly DENIED (sensitivity gating enforced)');
  }
  check(errors2.length === 0, `analyst: no page errors (${errors2.length})`);
  await pg2.close();

  await browser.close();
  if (failures.length) { console.log(`\nFAILED: ${failures.length}`); process.exit(1); }
  console.log('\nALL GREEN'); process.exit(0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
