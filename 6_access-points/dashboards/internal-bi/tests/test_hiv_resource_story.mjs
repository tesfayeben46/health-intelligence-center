// Role-story walk: abebe.bekele (R-PD-HIV, programme=HIV cap) must get a
// working ARV-supply Resource page: HIV commodities only in every widget.
import { chromium } from 'playwright';
const SHOTS = '/tmp/nhdw-test-shots';
let allOk = true;
const check = (b, m) => { console.log(`${b ? 'PASS' : 'FAIL'}: ${m}`); if (!b) allOk = false; };
const HIV_COMMODITIES = new Set(['TLD · adult ART', 'Pediatric ART', 'HIV RDT kit']);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
const cap = {};
ctx.on('response', async (r) => {
  for (const k of ['rm-hero', 'rm-stock-heatmap', 'rm-events', 'rm-stockout-impact', 'rm-statboard']) {
    if (r.url().includes(k) && r.status() === 200) {
      try { (cap[k] ||= []).push(await r.json()); } catch {}
    }
  }
});
const page = await ctx.newPage();
try {
  await page.goto('https://internal-bi.127.0.0.1.nip.io/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('#username', { timeout: 20000 });
  await page.fill('#username', 'abebe.bekele');
  await page.fill('#password', process.env.SEED_PASSWORD);
  await page.press('#password', 'Enter');
  await page.waitForLoadState('networkidle', { timeout: 40000 }).catch(() => {});
  for (let i = 0; i < 3; i++) {
    const retry = page.locator('button, a').filter({ hasText: /try again/i }).first();
    if (!(await retry.count())) break;
    await retry.click();
    await page.waitForLoadState('networkidle', { timeout: 40000 }).catch(() => {});
  }
  const navEl = page.locator('nav, aside, [class*=side], [class*=nav]').getByText('Resource', { exact: false }).first();
  check(await navEl.count() > 0, 'Resource page visible to R-PD-HIV (page elision grants it)');
  await navEl.click();
  await page.waitForTimeout(3000);
  for (let y = 0; y < 6; y++) { await page.mouse.wheel(0, 1200); await page.waitForTimeout(500); }
  await page.waitForTimeout(2500);

  const heat = (cap['rm-stock-heatmap'] || []).flatMap(p => p?.data?.rows || []);
  const heatComms = new Set(heat.map(r => r.commodity_name));
  check(heat.length > 0, `heatmap rows for HIV director (${heat.length})`);
  check([...heatComms].every(c => HIV_COMMODITIES.has(c)),
    `heatmap shows only HIV commodities (${[...heatComms].join(' | ')})`);

  const hero = (cap['rm-hero'] || []).flatMap(p => p?.data?.rows || []);
  check(hero.length > 0, 'hero returns a scoped stockout-watch row');

  const txt = await page.locator('body').innerText();
  check(!/Artemether|BCG vaccine|Oxytocin/i.test(txt), 'no out-of-programme commodities leak into the page');
  await page.screenshot({ path: `${SHOTS}/p2_hiv_resource.png` });
} catch (e) { console.log('FAIL: exception: ' + e.message); allOk = false; }
await browser.close();
console.log(allOk ? 'ALL OK' : 'SOME CHECKS FAILED');
process.exit(allOk ? 0 : 1);
