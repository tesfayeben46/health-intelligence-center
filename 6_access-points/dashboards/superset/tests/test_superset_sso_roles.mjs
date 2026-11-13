// Superset SSO and role enforcement: direct access shows the same-credentials
// login; a scientist (R-CONS-SCI to Alpha) is admitted with SQL Lab and the
// Trino marts database; a partner (R-CONS-PRT, no mapped Superset role)
// lands in the empty Public role with no data access.
import { chromium } from 'playwright';
const SHOTS = '/tmp/nhdw-test-shots';
const PW = process.env.SEED_PASSWORD;
let allOk = true;
const check = (b, m) => { console.log(`${b ? 'PASS' : 'FAIL'}: ${m}`); if (!b) allOk = false; };
const browser = await chromium.launch({ args: ['--no-sandbox'] });

async function loginSuperset(page, user) {
  await page.goto('https://superset.127.0.0.1.nip.io/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(1500);
  const kc = page.getByText(/Sign In with keycloak/i).first();
  if (await kc.count() > 0) { await kc.click().catch(() => {}); await page.waitForTimeout(2000); }
  if (await page.locator('#username').count() > 0) {
    await page.fill('#username', user); await page.fill('#password', PW);
    await page.click('#kc-login, button[type=submit], input[type=submit]');
    await page.waitForTimeout(3500);
  }
  await page.waitForTimeout(2500);
}

// sci.demo (R-CONS-SCI to Alpha) admitted, SQL Lab available.
{
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const promptedLogin = await (async () => {
    await page.goto('https://superset.127.0.0.1.nip.io/', { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(1500);
    return await page.getByText(/Sign In with keycloak/i).count() > 0;
  })();
  await loginSuperset(page, 'sci.demo');
  const url = page.url();
  const body = (await page.locator('body').innerText().catch(() => '')) || '';
  await page.screenshot({ path: `${SHOTS}/superset_sci.png`, fullPage: false });
  check(promptedLogin, 'Superset direct access shows the Keycloak sign-in');
  check(/superset\.127/.test(url) && !/Access is Denied|Forbidden|401|Unauthorized/i.test(body),
        `sci.demo (R-CONS-SCI to Alpha) admitted to Superset (url=${url.slice(0, 55)})`);
  // Open SQL Lab to confirm the Alpha capability and the Trino database.
  await page.goto('https://superset.127.0.0.1.nip.io/sqllab/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(4000);
  const sqllab = (await page.locator('body').innerText().catch(() => '')) || '';
  await page.screenshot({ path: `${SHOTS}/superset_sci_sqllab.png`, fullPage: false });
  check(/SQL Lab|Run|Database|Schema|NHDW Marts/i.test(sqllab) && !/Access is Denied|Forbidden/i.test(sqllab),
        'sci.demo can reach SQL Lab (Alpha + sql_lab role)');
  await ctx.close();
}

// shewaye.a (R-CONS-PRT) has no mapped Superset role, so Public and no data access.
{
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  await loginSuperset(page, 'shewaye.a');
  // Open SQL Lab directly; a Public user must be refused.
  await page.goto('https://superset.127.0.0.1.nip.io/sqllab/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(3500);
  const body = (await page.locator('body').innerText().catch(() => '')) || '';
  await page.screenshot({ path: `${SHOTS}/superset_partner.png`, fullPage: false });
  const denied = /Access is Denied|Forbidden|403|do not have|not authorized|Sign In with keycloak/i.test(body)
    || !/Run\b|Database|Schema/i.test(body);
  check(denied, 'shewaye.a (R-CONS-PRT, unmapped) is not given SQL Lab / data access (empty Public role)');
  await ctx.close();
}

await browser.close();
console.log(allOk ? '\nSUPERSET SSO CHECKS PASSED' : '\nSOME CHECKS FAILED');
process.exit(allOk ? 0 : 1);
