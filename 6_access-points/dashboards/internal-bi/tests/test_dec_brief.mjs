// Role-story walk: dec.nat (R-DEC, the policy-maker persona); the
// Overview must open with a cross-domain decision brief.
import { chromium } from 'playwright';
const SHOTS = '/tmp/nhdw-test-shots';
let allOk = true;
const check = (b, m) => { console.log(`${b ? 'PASS' : 'FAIL'}: ${m}`); if (!b) allOk = false; };
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
const cap = [];
ctx.on('response', async (r) => {
  if (r.url().includes('ov-brief') && r.status() === 200) {
    try { cap.push(await r.json()); } catch {}
  }
});
const page = await ctx.newPage();
try {
  await page.goto('https://internal-bi.127.0.0.1.nip.io/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('#username', { timeout: 20000 });
  await page.fill('#username', 'dec.nat');
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
  await page.waitForTimeout(4500);
  const rows = cap.flatMap(p => p?.data?.rows || []);
  check(rows.length >= 4, `brief returns cross-domain lines (${rows.length})`);
  const kinds = new Set(rows.map(r => r.kind));
  check(['act', 'supply', 'trust'].every(k => kinds.has(k)),
    `brief spans domains (${[...kinds].join(',')})`);
  const txt = await page.locator('body').innerText();
  check(/act first:/i.test(txt), 'Act-first line rendered');
  check(/completeness/i.test(txt), 'reporting-trust line rendered');
  await page.screenshot({ path: `${SHOTS}/p2_dec_brief.png` });
} catch (e) { console.log('FAIL: exception: ' + e.message); allOk = false; }
await browser.close();
console.log(allOk ? 'ALL OK' : 'SOME CHECKS FAILED');
process.exit(allOk ? 0 : 1);
