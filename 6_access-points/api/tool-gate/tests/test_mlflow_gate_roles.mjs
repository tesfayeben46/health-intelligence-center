// Tool-gate checks for MLflow (labels as in the output):
//  R1 direct access shows the login (same credentials); R3 login lands in
//  MLflow; R4a allowed role (sci) admitted; R4b disallowed role (steward) gets
//  403. R2, button visibility, is a widget grant checked separately.
import { chromium } from 'playwright';
const SHOTS = '/tmp/nhdw-test-shots';
let allOk = true;
const check = (b, m) => { console.log(`${b ? 'PASS' : 'FAIL'}: ${m}`); if (!b) allOk = false; };
const browser = await chromium.launch({ args: ['--no-sandbox'] });

async function loginAs(user) {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  // the bare MLflow URL should redirect to the Keycloak login
  await page.goto('https://mlflow.127.0.0.1.nip.io/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(2500);
  const sawLogin = await page.locator('#username').count() > 0;
  if (sawLogin) {
    await page.fill('#username', user);
    await page.fill('#password', process.env.SEED_PASSWORD);
    await page.press('#password', 'Enter');
    await page.waitForLoadState('networkidle', { timeout: 40000 }).catch(() => {});
    await page.waitForTimeout(3500);
  }
  const body = await page.locator('body').innerText();
  const url = page.url();
  await page.screenshot({ path: `${SHOTS}/gate_${user.replace(/\W/g,'_')}.png` });
  await ctx.close();
  return { sawLogin, body, url };
}

try {
  // R1: direct access with no session shows the login page
  const sci = await loginAs('sci.demo');   // R-CONS-SCI (moh-staff), allowed
  check(sci.sawLogin, 'R1/R3: bare MLflow URL shows Keycloak login (no session)');
  check(/experiments|models|mlflow|ears|ewars/i.test(sci.body), `R3/R4a: allowed role (sci.demo) lands in MLflow (${sci.body.slice(0,50).replace(/\n/g,' ')})`);
  check(!/access denied|role required|don't have access/i.test(sci.body), 'R4a: allowed role not blocked');

  // R4b: a valid login whose role is not permitted gets the 403 page
  const stew = await loginAs('demo.steward');  // R-STEW, not in MLflow ALLOWED_ROLES
  check(/don't have access|access denied|role/i.test(stew.body), `R4b: disallowed role (demo.steward) is DENIED (${stew.body.slice(0,60).replace(/\n/g,' ')})`);
  check(!/experiments|ears|ewars/i.test(stew.body), 'R4b: disallowed role never reaches MLflow');
} catch (e) { console.log('FAIL: exception; ' + e.message); allOk = false; }
await browser.close();
console.log(allOk ? 'ALL OK' : 'SOME CHECKS FAILED');
process.exit(allOk ? 0 : 1);
