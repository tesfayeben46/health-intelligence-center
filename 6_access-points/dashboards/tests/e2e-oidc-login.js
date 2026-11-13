// e2e-oidc-login.js: end-to-end check that a user can sign in to the dashboards.
//
// bundle/scripts/verify-system.sh proves each SPA answers over HTTPS, but an
// ingress can return 200 while the OIDC round trip is broken. This test drives
// the Keycloak login form, the redirect back and the rendered content, and keeps
// one browser context throughout, so public and workshop must reuse the
// internal-bi session (shared SSO) rather than prompt for a login.
//
// admin is checked the other way round. It authenticates against the
// privileged-ops realm, not moh-staff, so a second login prompt there means the
// realm boundary holds; superadmin exists in both realms. Silent
// entry to admin would be a security regression.
//
// Needs playwright and network reach to the deployment (run it on the host).
//   NHDW_HOSTBASE=<public-ip>.nip.io node e2e-oidc-login.js
// Env: NHDW_HOSTBASE, SEED_USER (default superadmin), SEED_PASSWORD
const { chromium } = require('playwright');
const HOST = process.env.NHDW_HOSTBASE || '127.0.0.1.nip.io';
const PASS = process.env.SEED_PASSWORD;
const USER = process.env.SEED_USER || 'superadmin';
const B = h => `https://${h}.${HOST}`;
let ok = 0, bad = 0;
const R = (good, name, note='') => { good ? ok++ : bad++;
  console.log(`  ${good ? 'ok  ' : 'FAIL'} ${name}${note ? '  [' + note + ']' : ''}`); };

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox', '--ignore-certificate-errors'] });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e).slice(0, 120)));

  await page.goto(B('internal-bi') + '/', { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(3000);
  const sawLogin = !!(await page.$('#username'));
  if (sawLogin) {
    await page.fill('#username', USER);
    await page.fill('#password', PASS);
    await page.click('#kc-login, button[type=submit]');
    await page.waitForTimeout(8000);
  }
  R(sawLogin, 'internal-bi redirected to the Keycloak login form');
  await page.waitForLoadState('networkidle', { timeout: 25000 }).catch(() => {});
  await page.waitForTimeout(2500);
  R(page.url().includes('internal-bi'), 'landed back on internal-bi after login', page.url().slice(0, 70));

  const bodyLen = (await page.textContent('body').catch(() => '') || '').trim().length;
  R(bodyLen > 400, 'internal-bi rendered real content', `${bodyLen} chars`);

  // public and workshop share internal-bi's moh-staff session; admin (below)
  // is a separate realm and must prompt again.
  for (const h of ['public', 'workshop']) {
    await page.goto(B(h) + '/', { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
    await page.waitForLoadState('networkidle', { timeout: 25000 }).catch(() => {});
    await page.waitForTimeout(3000);
    const t = (await page.textContent('body').catch(() => '') || '').trim();
    const onLogin = !!(await page.$('#username'));
    R(t.length > 300 && !onLogin, `${h} rendered via the existing moh-staff session`,
      onLogin ? 'asked to log in again: SSO not shared' : `${t.length} chars`);
  }

  await page.goto(B('admin') + '/', { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(3500);
  const adminAsksLogin = !!(await page.$('#username'));
  R(adminAsksLogin, 'admin demands its own login (privileged-ops realm boundary held)');
  if (adminAsksLogin) {
    await page.fill('#username', USER);
    await page.fill('#password', PASS);
    await page.click('#kc-login, button[type=submit]');
    await page.waitForTimeout(8000);
  }
  await page.waitForLoadState('networkidle', { timeout: 25000 }).catch(() => {});
  await page.waitForTimeout(2500);
  const at = (await page.textContent('body').catch(() => '') || '').trim();
  R(page.url().includes('admin') && at.length > 300 && !(await page.$('#username')),
    'admin rendered after privileged-ops login', `${at.length} chars`);

  if (errors.length) console.log('  page errors:', errors.slice(0, 4).join(' | '));
  console.log(`\n  ${ok} passed, ${bad} failed`);
  await browser.close();
  process.exit(bad ? 1 : 0);
})();
