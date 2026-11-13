/**
 * Regression: catalog-driven Service page + chrome global-filter + value-level
 * scoping. Verifies the full chain, not just DOM structure:
 *   · widgets show real mart data (echarts canvas / table rows with values),
 *     never synthetic, never an empty card passed off as "present";
 *   · the global filter is a chrome WIDGET whose options honor per-role
 *     allowed_values (R-PD-HIV: Programme pinned+LOCKED to HIV);
 *   · OPA row_filter actually scopes the DATA (R-PD-HIV flagship = HIV only).
 *
 * R-DEC (tadesse.h): full data, unlocked filters.
 * R-PD-HIV (abebe.bekele): Programme LOCKED=HIV, HIV data present, flagship
 *                            Programme column is 100% HIV, TB/MNCH/NCD funnels
 *                            absent (permission reflow).
 *
 * Run: NODE_PATH=node_modules \
 *      PLAYWRIGHT_BROWSERS_PATH=~/.cache/ms-playwright <vscode-node> test_catalog_service.cjs
 */
const { chromium } = require('playwright');

const BI = 'https://internal-bi.127.0.0.1.nip.io/';
const PW = process.env.SEED_PASSWORD;
const failures = [];
const check = (c, l) => { console.log((c ? '  ok   ' : '  FAIL ') + l); if (!c) failures.push(l); };

async function openService(browser, user) {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1500, height: 1200 } });
  const pg = await ctx.newPage();
  await pg.goto(BI, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await pg.waitForSelector('#username', { timeout: 30000 });
  await pg.fill('#username', user);
  await pg.fill('#password', PW);
  await pg.click('#kc-login');
  await pg.waitForFunction(() => {
    const noAccess = document.body.innerText.includes('No access to Internal BI');
    return noAccess || (document.querySelectorAll('.bi-nav a').length > 0 && !document.querySelector('#username'));
  }, { timeout: 60000 });
  await pg.waitForTimeout(4000);
  if (await pg.locator('text=No access to Internal BI').count() > 0) return { pg, ctx, denied: true };
  const nav = pg.locator('.bi-nav a', { hasText: 'Service Performance' });
  try { await nav.waitFor({ timeout: 20000 }); } catch { return { pg, ctx, denied: true }; }
  await nav.click({ timeout: 15000 });
  await pg.waitForSelector('.catalog-page .page-section', { timeout: 20000 }).catch(() => {});
  await pg.waitForTimeout(6000);
  return { pg, ctx, denied: false };
}

function scrape(pg) {
  return pg.evaluate(() => {
    const sections = [...document.querySelectorAll('.catalog-page .page-section')].map(s => ({
      id: s.dataset.sectionId,
      widgets: [...s.querySelectorAll('[data-widget-id]')].map(w => ({
        id: w.dataset.widgetId,
        canvas: !!w.querySelector('canvas'),
        rows: w.querySelectorAll('table.dash tbody tr').length,
        empty: !!w.querySelector('.widget-empty'),
        // flagship table: collect the Programme column (2nd col) values
        progCol: [...w.querySelectorAll('table.dash tbody tr')].map(tr => tr.children[1]?.textContent?.trim()).filter(Boolean),
      })),
    }));
    const chips = [...document.querySelectorAll('.chrome-widget .filter-chip')].map(c => ({
      key: c.querySelector('.key')?.textContent?.trim(),
      val: c.querySelector('.val')?.textContent?.trim(),
      locked: c.classList.contains('locked'),
    }));
    // Reflow: the fraction of the cascades band width that sv-cascade-hiv fills.
    const cascGrid = document.querySelector('.page-section[data-section-id$="/cascades"] .ps-grid');
    const hivCard = document.querySelector('[data-widget-id="sv-cascade-hiv"]');
    const hivFillFrac = (cascGrid && hivCard)
      ? hivCard.getBoundingClientRect().width / cascGrid.getBoundingClientRect().width : 0;
    return {
      noRenderer: document.body.innerText.includes('No renderer registered'),
      loadingStuck: document.body.innerText.includes('Loading…'),
      sections, chips, hivFillFrac,
    };
  });
}
const widgetOf = (r, id) => r.sections.flatMap(s => s.widgets).find(w => w.id === id);

(async () => {
  const browser = await chromium.launch();

  // R-DEC · full
  {
    const { pg, ctx } = await openService(browser, 'tadesse.h');
    const r = await scrape(pg);
    check(r.sections.length === 3, `R-DEC: 3 sections (${r.sections.length})`);
    const flag = widgetOf(r, 'sv-flagship-table');
    check(flag && flag.rows >= 10 && !flag.empty, `R-DEC: flagship real rows (${flag?.rows})`);
    const cascN = r.sections.find(s => s.id.endsWith('/cascades'))?.widgets.filter(w => w.canvas && !w.empty).length;
    check(cascN === 4, `R-DEC: 4 cascade funnels drawn (${cascN})`);
    const prg = r.chips.find(c => /programme/i.test(c.key || ''));
    check(prg && !prg.locked, `R-DEC: Programme filter not locked (val=${prg?.val})`);
    check(r.hivFillFrac > 0.35 && r.hivFillFrac < 0.6, `R-DEC: 4-funnel cascades keep designed ~half width (${(r.hivFillFrac * 100).toFixed(0)}%)`);
    check(!r.noRenderer && !r.loadingStuck, 'R-DEC: no renderer gaps / no stuck loading');
    await ctx.close();
  }

  // R-PD-HIV · scoped
  {
    const { pg, ctx, denied } = await openService(browser, 'abebe.bekele');
    check(!denied, 'R-PD-HIV: reaches the Service page');
    if (!denied) {
      const r = await scrape(pg);
      // (1) global filter scoped: Programme pinned + LOCKED to HIV
      const prg = r.chips.find(c => /programme/i.test(c.key || ''));
      check(!!prg && prg.locked && /HIV/i.test(prg.val || ''),
        `R-PD-HIV: Programme filter LOCKED to HIV (locked=${prg?.locked}, val=${prg?.val})`);
      const region = r.chips.find(c => /region|geograph/i.test(c.key || ''));
      check(!!region && !region.locked, `R-PD-HIV: Region filter not locked (wildcard): val=${region?.val}`);
      // (2) HIV data actually present (not an empty card)
      const hiv = widgetOf(r, 'sv-cascade-hiv');
      check(!!hiv && hiv.canvas && !hiv.empty, `R-PD-HIV: HIV cascade funnel DRAWN with data (canvas=${hiv?.canvas}, empty=${hiv?.empty})`);
      const flag = widgetOf(r, 'sv-flagship-table');
      check(!!flag && flag.rows > 0 && !flag.empty, `R-PD-HIV: flagship table has data (${flag?.rows} rows)`);
      // (3) OPA row_filter really scopes the DATA: every flagship row is HIV
      const allHiv = flag && flag.progCol.length > 0 && flag.progCol.every(v => v === 'HIV');
      check(allHiv, `R-PD-HIV: flagship Programme column is 100% HIV: got [${[...new Set(flag?.progCol || [])].join(', ')}]`);
      // (4) permission reflow: TB/MNCH/NCD funnels absent
      const casc = r.sections.find(s => s.id.endsWith('/cascades'))?.widgets.map(w => w.id) || [];
      check(!casc.includes('sv-cascade-tb') && !casc.includes('sv-cascade-mnch') && !casc.includes('sv-cascade-ncd'),
        `R-PD-HIV: TB/MNCH/NCD funnels absent: cascades=[${casc.join(', ')}]`);
      // (5) reflow-to-fill: the lone HIV funnel grows to (near) the full band width
      check(r.hivFillFrac > 0.9, `R-PD-HIV: HIV funnel fills the cascades band width (${(r.hivFillFrac * 100).toFixed(0)}%)`);
      check(!r.noRenderer, 'R-PD-HIV: no renderer gaps');
    }
    await ctx.close();
  }

  await browser.close();
  if (failures.length) { console.log(`\nFAILED: ${failures.length}`); process.exit(1); }
  console.log('\nALL GREEN'); process.exit(0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
