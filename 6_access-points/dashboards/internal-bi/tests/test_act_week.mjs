// rm-act-week hero verification: analyst (national) + kebede.oromia (scoped:
// the role-story test: an RHB director must see their outbreak-supply gaps).
import { chromium } from 'playwright';
const SHOTS = '/tmp/nhdw-test-shots';
let allOk = true;
const check = (b, m) => { console.log(`${b ? 'PASS' : 'FAIL'}: ${m}`); if (!b) allOk = false; };
const browser = await chromium.launch({ args: ['--no-sandbox'] });

async function walk(user, expectRegions) {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  const cap = [];
  ctx.on('response', async (r) => {
    if (r.url().includes('rm-act-week') && r.status() === 200) {
      try { cap.push(await r.json()); } catch {}
    }
  });
  const page = await ctx.newPage();
  await page.goto('https://internal-bi.127.0.0.1.nip.io/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('#username', { timeout: 20000 });
  await page.fill('#username', user);
  await page.fill('#password', process.env.SEED_PASSWORD);
  await page.press('#password', 'Enter');
  await page.waitForLoadState('networkidle', { timeout: 40000 }).catch(() => {});
  for (let i = 0; i < 3; i++) {
    const retry = page.locator('button, a').filter({ hasText: /try again/i }).first();
    if (!(await retry.count())) break;
    await retry.click();
    await page.waitForLoadState('networkidle', { timeout: 40000 }).catch(() => {});
  }
  await page.locator('nav, aside, [class*=side], [class*=nav]').getByText('Resource', { exact: false }).first().click();
  await page.waitForTimeout(4000);
  const rows = cap.flatMap(p => p?.data?.rows || []);
  const regions = new Set(rows.map(r => r.region_id));
  check(rows.length > 0, `${user}: rm-act-week returns rows (${rows.length})`);
  const txt = await page.locator('body').innerText();
  check(/act this week/i.test(txt), `${user}: Act This Week section renders`);
  if (expectRegions === 'OR-only') {
    check(regions.size === 1 && regions.has('OR'), `${user}: rows scoped to Oromia only (${[...regions].join(',')})`);
  } else {
    check(regions.size >= 5, `${user}: national spread (${regions.size} regions)`);
  }
  await page.screenshot({ path: `${SHOTS}/p2_actweek_${user.replace(/\W/g, '_')}.png` });
  await ctx.close();
  return rows;
}

try {
  const nat = await walk('analyst.ana', 'national');
  const top = nat[0];
  check(top && Number(top.act_score) >= Number(nat[nat.length - 1]?.act_score ?? 0), 'ranked by act_score desc');
  await walk('kebede.oromia', 'OR-only');
} catch (e) { console.log('FAIL: exception: ' + e.message); allOk = false; }
await browser.close();
console.log(allOk ? 'ALL OK' : 'SOME CHECKS FAILED');
process.exit(allOk ? 0 : 1);
