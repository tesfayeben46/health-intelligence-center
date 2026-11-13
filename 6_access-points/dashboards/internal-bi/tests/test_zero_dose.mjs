import { chromium } from 'playwright';
const SHOTS = '/tmp/nhdw-test-shots';
let allOk = true;
const check = (b, m) => { console.log(`${b ? 'PASS' : 'FAIL'}: ${m}`); if (!b) allOk = false; };
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
const cap = [];
ctx.on('response', async (r) => {
  if (r.url().includes('sv-zero-dose') && r.status() === 200) {
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
    const r = page.locator('button, a').filter({ hasText: /try again/i }).first();
    if (!(await r.count())) break;
    await r.click();
    await page.waitForLoadState('networkidle', { timeout: 40000 }).catch(() => {});
  }
  await page.locator('nav, aside, [class*=side]').getByText('Service', { exact: false }).first().click();
  await page.waitForTimeout(2500);
  for (let y = 0; y < 8; y++) { await page.mouse.wheel(0, 1100); await page.waitForTimeout(350); }
  await page.waitForTimeout(3500);
  const rows = cap.flatMap(p => p?.data?.rows || []);
  check(rows.length >= 10, `zero-dose rows (${rows.length} regions)`);
  const reaches = rows.map(r => Number(r.reach_index)).filter(n => !isNaN(n));
  check(Math.max(...reaches) > Math.min(...reaches) * 2, `reach differentiates (${Math.min(...reaches)} to ${Math.max(...reaches)})`);
  check(rows.some(r => r.mbrana_zero_stock_pct != null), 'mBrana stress present where USSD covers');
  const txt = await page.locator('body').innerText();
  check(/labeled estimate|crude birth/i.test(txt), 'estimate labeling visible');
  await page.screenshot({ path: `${SHOTS}/p3_zero_dose.png` });
} catch (e) { console.log('FAIL: exception: ' + e.message); allOk = false; }
await browser.close();
console.log(allOk ? 'ALL OK' : 'SOME CHECKS FAILED');
process.exit(allOk ? 0 : 1);
