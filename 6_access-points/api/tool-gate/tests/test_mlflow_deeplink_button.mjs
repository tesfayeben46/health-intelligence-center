// Deep-link flow and button visibility (R1, R2 in the output):
//  - sci.demo (allowed): MLflow button visible on Help/Tools; clicking it while
//    logged into workshop lands in MLflow with no second login.
//  - demo.steward (not allowed MLflow): MLflow button hidden; dbt Docs shown.
import { chromium } from 'playwright';
const SHOTS = '/tmp/nhdw-test-shots';
let allOk = true;
const check = (b, m) => { console.log(`${b ? 'PASS' : 'FAIL'}: ${m}`); if (!b) allOk = false; };
const browser = await chromium.launch({ args: ['--no-sandbox'] });

async function openTools(user) {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  await page.goto('https://workshop.127.0.0.1.nip.io/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  if (!(await page.locator('#username').count())) {
    const b = page.locator('button, a').filter({ hasText: /sign in|log in/i }).first();
    if (await b.count()) await b.click();
  }
  await page.waitForSelector('#username', { timeout: 20000 });
  await page.fill('#username', user); await page.fill('#password', process.env.SEED_PASSWORD); await page.press('#password', 'Enter');
  await page.waitForLoadState('networkidle', { timeout: 40000 }).catch(()=>{});
  for (let i=0;i<3;i++){const r=page.locator('button,a').filter({hasText:/try again/i}).first();if(!(await r.count()))break;await r.click();await page.waitForLoadState('networkidle',{timeout:40000}).catch(()=>{});}
  await page.waitForTimeout(2000);
  // navigate to Help/Tools
  const help = page.locator('nav, aside, [class*=side]').getByText(/help/i).first();
  if (await help.count()) { await help.click(); await page.waitForTimeout(1500); }
  const tools = page.locator('nav, aside, [class*=side], a, button').getByText(/tools/i).first();
  if (await tools.count()) { await tools.click(); await page.waitForTimeout(1500); }
  await page.waitForTimeout(1500);
  return { ctx, page };
}

try {
  // sci.demo: MLflow button visible, deep link logs in automatically
  const s = await openTools('sci.demo');
  const sBody = await s.page.locator('body').innerText();
  const mlflowBtn = s.page.locator('a').filter({ hasText: /open/i }).and(s.page.locator('a[href*="mlflow"]'));
  const hasMlflowBtn = /MLflow/i.test(sBody) && await s.page.locator('a[href*="mlflow.127.0.0.1"]').count() > 0;
  check(hasMlflowBtn, 'R2: MLflow button VISIBLE for sci.demo');
  await s.page.screenshot({ path: `${SHOTS}/ws_tools_sci.png`, fullPage: true });
  if (hasMlflowBtn) {
    const [tab] = await Promise.all([
      s.ctx.waitForEvent('page', { timeout: 20000 }).catch(()=>null),
      s.page.locator('a[href*="mlflow.127.0.0.1"]').first().click(),
    ]);
    const t = tab || s.page;
    await t.waitForLoadState('networkidle', { timeout: 40000 }).catch(()=>{});
    await t.waitForTimeout(4000);
    const tb = await t.locator('body').innerText();
    check(!/#username|sign in with your/i.test(tb) && /experiments|models|mlflow/i.test(tb),
      `R1: deep-link auto-login; no 2nd login, landed in MLflow (${tb.slice(0,45).replace(/\n/g,' ')})`);
    await t.screenshot({ path: `${SHOTS}/ws_mlflow_deeplink.png` });
  }
  await s.ctx.close();

  // demo.steward: MLflow button hidden, dbt Docs shown
  const st = await openTools('demo.steward');
  const stBody = await st.page.locator('body').innerText();
  const stewMlflowBtn = await st.page.locator('a[href*="mlflow.127.0.0.1"]').count();
  check(stewMlflowBtn === 0, `R2: MLflow button HIDDEN for demo.steward (found ${stewMlflowBtn})`);
  check(/dbt Docs/i.test(stBody), 'R2: dbt Docs still shown to demo.steward (allowed)');
  await st.page.screenshot({ path: `${SHOTS}/ws_tools_steward.png`, fullPage: true });
  await st.ctx.close();
} catch (e) { console.log('FAIL: exception; ' + e.message); allOk = false; }
await browser.close();
console.log(allOk ? 'ALL OK' : 'SOME CHECKS FAILED');
process.exit(allOk ? 0 : 1);
