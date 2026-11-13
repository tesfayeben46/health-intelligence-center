// R-CONS-PRT (shewaye.a): the Service page must show cascade and flagship
// data (aggregate PRG reads), not empty cards, while raw/individual data stays
// denied (no supply/facility raw grants).
import { chromium } from 'playwright';
const SHOTS = '/tmp/nhdw-test-shots';
let allOk = true;
const check = (b, m) => { console.log(`${b ? 'PASS' : 'FAIL'}: ${m}`); if (!b) allOk = false; };
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 900 } });
const cap = {};
ctx.on('response', async (r) => {
  for (const k of ['sv-cascade-hiv', 'sv-cascade-tb', 'sv-flagship-table', 'dz-signal-chart']) {
    if (r.url().includes(k) && r.url().includes('/data')) {
      try { (cap[k] ||= []).push({ status: r.status(), body: r.status()===200 ? await r.json() : null }); } catch {}
    }
  }
});
const page = await ctx.newPage();
try {
  await page.goto('https://internal-bi.127.0.0.1.nip.io/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('#username', { timeout: 20000 });
  await page.fill('#username', 'shewaye.a');
  await page.fill('#password', process.env.SEED_PASSWORD);
  await page.press('#password', 'Enter');
  await page.waitForLoadState('networkidle', { timeout: 40000 }).catch(() => {});
  await page.waitForTimeout(2500);
  await page.locator('nav, aside, [class*=side]').getByText('Service', { exact: false }).first().click();
  await page.waitForTimeout(4000);
  const hiv = (cap['sv-cascade-hiv'] || []);
  const flag = (cap['sv-flagship-table'] || []);
  const hivRows = hiv.flatMap(c => c.body?.data?.rows || []);
  check(hiv.some(c => c.status === 200), `HIV cascade returns 200 (was 403): ${hiv.map(c=>c.status).join(',')}`);
  check(hivRows.length > 0, `HIV cascade has real rows (${hivRows.length})`);
  check(flag.some(c => c.status === 200), `flagship returns 200: ${flag.map(c=>c.status).join(',')}`);
  const body = await page.locator('body').innerText();
  check(!/No cascade data for the selected filters/i.test(body) || hivRows.length > 0, 'cascades no longer all-empty husks');
  await page.screenshot({ path: `${SHOTS}/p_partner_fixed.png`, fullPage: true });
  // negative: partner must not be able to read a raw/supply widget they were never granted
  const denied = await page.evaluate(async () => {
    const r = await fetch('/gw/permissions/widget/rm-events/data?dashboard_id=internal-bi&page_id=internal-bi%2Fresource', { credentials: 'include', headers: { Authorization: 'Bearer ' + (window.__nhdwAccessToken||'') } });
    return r.status;
  });
  check(denied === 403 || denied === 401 || denied === 404, `partner still denied ungranted supply widget (rm-events: ${denied})`);
} catch (e) { console.log('FAIL: exception: ' + e.message); allOk = false; }
await browser.close();
console.log(allOk ? 'ALL OK' : 'SOME CHECKS FAILED');
process.exit(allOk ? 0 : 1);
