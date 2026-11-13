// ov-source-pulse trust tiles on Overview (analyst).
import { chromium } from 'playwright';
const SHOTS = '/tmp/nhdw-test-shots';
let allOk = true;
const check = (b, m) => { console.log(`${b ? 'PASS' : 'FAIL'}: ${m}`); if (!b) allOk = false; };
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
const cap = [];
ctx.on('response', async (r) => {
  if (r.url().includes('ov-source-pulse') && r.status() === 200) {
    try { cap.push(await r.json()); } catch {}
  }
});
const page = await ctx.newPage();
try {
  await page.goto('https://internal-bi.127.0.0.1.nip.io/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('#username', { timeout: 20000 });
  await page.fill('#username', 'analyst.ana');
  await page.fill('#password', process.env.SEED_PASSWORD);
  await page.press('#password', 'Enter');
  await page.waitForLoadState('networkidle', { timeout: 40000 }).catch(() => {});
  for (let i = 0; i < 3; i++) {
    const retry = page.locator('button, a').filter({ hasText: /try again/i }).first();
    if (!(await retry.count())) break;
    await retry.click();
    await page.waitForLoadState('networkidle', { timeout: 40000 }).catch(() => {});
  }
  await page.waitForTimeout(2000);
  await page.locator('nav, aside, [class*=side], [class*=nav]').getByText('Overview', { exact: false }).first().click();
  await page.waitForTimeout(2000);
  for (let y = 0; y < 6; y++) { await page.mouse.wheel(0, 1000); await page.waitForTimeout(400); }
  await page.waitForTimeout(3000);
  const rows = cap.flatMap(p => p?.data?.rows || []);
  check(rows.length >= 5, `source tiles returned (${rows.length} sources)`);
  const sources = new Set(rows.map(r => r.source));
  check(sources.has('mBrana') && sources.has('DHIS2'), `covers the feeds (${[...sources].join(', ')})`);
  const mb = rows.find(r => r.source === 'mBrana');
  check(mb && mb.status === 'poor', `mBrana flagged poor (${mb?.completeness_pct}%)`);
  const ew = rows.find(r => String(r.source).includes('Climate'));
  check(ew && ew.status === 'no_metric', 'EWARS/Climate shows no-metric');
  await page.screenshot({ path: `${SHOTS}/p3_source_pulse.png` });
} catch (e) { console.log('FAIL: exception: ' + e.message); allOk = false; }
await browser.close();
console.log(allOk ? 'ALL OK' : 'SOME CHECKS FAILED');
process.exit(allOk ? 0 : 1);
