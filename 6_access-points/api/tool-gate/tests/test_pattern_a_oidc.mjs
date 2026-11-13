// Tools with their own role-aware OIDC (not behind a tool-gate) enforce roles on
// direct access, including after a Keycloak reseed: Airflow admits an ops user;
// JupyterHub denies a moh-staff role not in allowed_groups (R-CONS-PRT) and
// admits a workshop role (R-CONS-SCI).
import { chromium } from 'playwright';
const SHOTS = '/tmp/nhdw-test-shots';
const PW = process.env.SEED_PASSWORD;
let allOk = true;
const check = (b, m) => { console.log(`${b ? 'PASS' : 'FAIL'}: ${m}`); if (!b) allOk = false; };
const browser = await chromium.launch({ args: ['--no-sandbox'] });

async function login(page, user) {
  await page.waitForTimeout(1500);
  if (await page.locator('#username').count() > 0) {
    await page.fill('#username', user); await page.fill('#password', PW);
    await page.click('#kc-login, button[type=submit], input[type=submit]');
    await page.waitForTimeout(3000);
  }
}
async function fresh() {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 850 } });
  return { ctx, page: await ctx.newPage() };
}

// Airflow: native OIDC (privileged-ops), R-OPS-ADM maps to Admin and sees DAGs
{
  const { ctx, page } = await fresh();
  await page.goto('https://airflow.127.0.0.1.nip.io/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(1500);
  const kc = page.getByText(/Sign In with keycloak/i).first();
  if (await kc.count() > 0) { await kc.click().catch(() => {}); await page.waitForTimeout(2000); }
  await login(page, 'solomon.m');
  await page.waitForTimeout(3000);
  const body = (await page.locator('body').innerText().catch(() => '')) || '';
  const url = page.url();
  await page.screenshot({ path: `${SHOTS}/patA_airflow_solomon.png` });
  check(/airflow\.127/.test(url) && /DAG|Airflow|Runs|Schedule/i.test(body) && !/Sign in|unauthor|forbidden/i.test(body),
        'Airflow admits R-OPS-ADM via SSO and shows the DAG surface');
  await ctx.close();
}

// JupyterHub, negative: R-CONS-PRT (shewaye.a) is not in allowed_groups
{
  const { ctx, page } = await fresh();
  await page.goto('https://jupyter.127.0.0.1.nip.io/hub/login', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await login(page, 'shewaye.a');
  await page.waitForTimeout(3000);
  const body = (await page.locator('body').innerText().catch(() => '')) || '';
  await page.screenshot({ path: `${SHOTS}/patA_jupyter_partner.png` });
  check(/403|not authoriz|forbidden|Access to.*not allowed|Sign in with|Please log in/i.test(body) || /realms\/moh-staff/.test(page.url()),
        `JupyterHub DENIES R-CONS-PRT (partner not in allowed_groups); no notebook (url=${page.url().slice(0, 60)})`);
  await ctx.close();
}

// JupyterHub, positive: R-CONS-SCI (sci.demo) is allowed
{
  const { ctx, page } = await fresh();
  await page.goto('https://jupyter.127.0.0.1.nip.io/hub/login', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await login(page, 'sci.demo');
  await page.waitForTimeout(4000);
  const body = (await page.locator('body').innerText().catch(() => '')) || '';
  const url = page.url();
  await page.screenshot({ path: `${SHOTS}/patA_jupyter_sci.png` });
  check(!/403|not authoriz|forbidden/i.test(body) && /jupyter\.127/.test(url),
        `JupyterHub admits R-CONS-SCI (spawner/lab, not denied); url=${url.slice(0, 60)}`);
  await ctx.close();
}

await browser.close();
console.log(allOk ? '\nPATTERN-A ENFORCEMENT CHECKS PASSED' : '\nSOME CHECKS FAILED');
process.exit(allOk ? 0 : 1);
