// Verify the ops tool-gates (Prometheus, Alertmanager, Headlamp): R1 silent
// cross-tool SSO from one login, R3 direct login with the same credentials,
// R4a allowed role admitted (including cross-realm), R4b denied role gets 403,
// and Headlamp's console loads with the service-account bearer (no token screen).
import { chromium } from 'playwright';
const SHOTS = '/tmp/nhdw-test-shots';
const PW = process.env.SEED_PASSWORD;
let allOk = true;
const check = (b, m) => { console.log(`${b ? 'PASS' : 'FAIL'}: ${m}`); if (!b) allOk = false; };
const browser = await chromium.launch({ args: ['--no-sandbox'] });

// Log in at a gate login URL if the Keycloak form appears; return whether a
// password prompt was shown (false means admitted silently on the shared session).
async function visit(page, url, { user } = {}) {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(1500);
  let promptedPassword = false;
  if (await page.locator('#username').count() > 0) {
    promptedPassword = true;
    await page.fill('#username', user);
    await page.fill('#password', PW);
    await page.click('#kc-login, button[type=submit], input[type=submit]');
    await page.waitForTimeout(2500);
  }
  await page.waitForLoadState('domcontentloaded').catch(() => {});
  await page.waitForTimeout(1500);
  return promptedPassword;
}

// Context A: one login (solomon.m, R-OPS-ADM, privileged-ops), three tools
{
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 850 } });
  const page = await ctx.newPage();

  // R3 + R4a: bare URL -> privileged-ops login -> Prometheus.
  const prompted1 = await visit(page, 'https://prometheus.127.0.0.1.nip.io/', { user: 'solomon.m' });
  const title1 = await page.title();
  await page.screenshot({ path: `${SHOTS}/gate_prom_solomon.png` });
  check(prompted1, 'R3 Prometheus direct access showed the Keycloak login');
  check(/prometheus/i.test(title1) && page.url().includes('prometheus.127.0.0.1.nip.io'),
        `R4a solomon.m (R-OPS-ADM) admitted to Prometheus (title="${title1}")`);

  // R1: same context, Alertmanager must not prompt again (shared SSO cookie).
  const prompted2 = await visit(page, 'https://alertmanager.127.0.0.1.nip.io/', { user: 'solomon.m' });
  const title2 = await page.title();
  await page.screenshot({ path: `${SHOTS}/gate_alert_solomon.png` });
  check(!prompted2, 'R1 Alertmanager admitted SILENTLY (no second login) off the shared session');
  check(/alertmanager/i.test(title2), `Alertmanager UI loaded (title="${title2}")`);

  // R1 + service-account bearer: same context, Headlamp console (admin host /k8s), no prompt.
  const prompted3 = await visit(page, 'https://admin.127.0.0.1.nip.io/oauth2gate/login?realm=privileged-ops&rd=/k8s/', { user: 'solomon.m' });
  await page.waitForTimeout(3500);
  const title3 = await page.title();
  const bodyTxt = (await page.locator('body').innerText().catch(() => '')) || '';
  const tokenScreen = /paste.*token|id[- ]?token|enter a token/i.test(bodyTxt);
  // Headlamp names the tab after the current view ("Cluster"), not "Headlamp",
  // so readiness is judged by cluster data and navigation, not the title.
  const consoleReady = /Overview|Namespaces|Workloads/.test(bodyTxt) && /Pods|Nodes/.test(bodyTxt);
  await page.screenshot({ path: `${SHOTS}/gate_headlamp_solomon.png` });
  check(!prompted3, 'R1 Headlamp admitted SILENTLY off the shared session');
  check(consoleReady && !tokenScreen,
        `Headlamp console loaded READY via SA bearer; live cluster data, no token screen (title="${title3}")`);
  await ctx.close();
}

// Context B: denied role (analyst.ana, R-CONS-ANA, moh-staff) gets 403
{
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 850 } });
  const page = await ctx.newPage();
  await visit(page, 'https://prometheus.127.0.0.1.nip.io/oauth2gate/login?realm=moh-staff&rd=/', { user: 'analyst.ana' });
  const body = (await page.locator('body').innerText().catch(() => '')) || '';
  await page.screenshot({ path: `${SHOTS}/gate_prom_denied.png` });
  check(/don.?t have access to prometheus/i.test(body),
        'R4b analyst.ana (R-CONS-ANA) gets the 403 "no access" page, never Prometheus');
  await ctx.close();
}

// Context C: cross-realm allow (yohannes.h, R-OPS-ENG, moh-staff) admitted
{
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 850 } });
  const page = await ctx.newPage();
  await visit(page, 'https://prometheus.127.0.0.1.nip.io/oauth2gate/login?realm=moh-staff&rd=/', { user: 'yohannes.h' });
  const title = await page.title();
  const body = (await page.locator('body').innerText().catch(() => '')) || '';
  await page.screenshot({ path: `${SHOTS}/gate_prom_yohannes.png` });
  check(/prometheus/i.test(title) && !/don.?t have access/i.test(body),
        `R4a cross-realm: yohannes.h (moh-staff R-OPS-ENG) admitted to Prometheus (title="${title}")`);
  await ctx.close();
}

await browser.close();
console.log(allOk ? '\nALL GATE CHECKS PASSED' : '\nSOME CHECKS FAILED');
process.exit(allOk ? 0 : 1);
