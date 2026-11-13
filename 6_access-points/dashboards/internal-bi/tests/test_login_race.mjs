// Login-race regression: a fresh login must reach the dashboard with no
// "Sign-in didn't complete" retry card and no pre-auth 401s on /spec or
// /alerts (the auth gates hold every authFetch until the token/session lands).
// Runs several fresh logins because the race is intermittent.
import { chromium } from 'playwright';
let allOk = true;
const check = (b, m) => { console.log(`${b ? 'PASS' : 'FAIL'}: ${m}`); if (!b) allOk = false; };
const browser = await chromium.launch({ args: ['--no-sandbox'] });

async function freshLogin(n) {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await ctx.newPage();
  const bad401 = [];
  page.on('response', r => {
    const u = r.url();
    if (r.status() === 401 && (/\/gw\/permissions\/dashboard\/.*\/spec/.test(u) || /\/gw\/alerts\//.test(u)))
      bad401.push(u.replace(/^https:\/\/[^/]+/, '').slice(0, 60));
  });
  await page.goto('https://internal-bi.127.0.0.1.nip.io/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('#username', { timeout: 20000 });
  await page.fill('#username', 'analyst.ana');
  await page.fill('#password', process.env.SEED_PASSWORD);
  await page.press('#password', 'Enter');
  await page.waitForLoadState('networkidle', { timeout: 40000 }).catch(() => {});
  await page.waitForTimeout(4000);
  const body = await page.locator('body').innerText();
  const retryCard = /Sign-in didn't complete/i.test(body);
  const reachedApp = /Overview|Disease Surveillance|Resource Management|Internal BI/i.test(body);
  check(!retryCard, `run ${n}: no retry card`);
  check(reachedApp, `run ${n}: dashboard reached`);
  check(bad401.length === 0, `run ${n}: no pre-auth 401 on spec/alerts (${bad401.length}${bad401.length ? ': ' + bad401.slice(0,2).join(',') : ''})`);
  await ctx.close();
}

try {
  for (let i = 1; i <= 3; i++) await freshLogin(i);
} catch (e) { console.log('FAIL: exception: ' + e.message); allOk = false; }
await browser.close();
console.log(allOk ? 'ALL OK' : 'SOME CHECKS FAILED');
process.exit(allOk ? 0 : 1);
