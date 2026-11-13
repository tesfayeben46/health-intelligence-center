/**
 * Access-request provisioning, end to end through the admin UI:
 *   public submit to admin Review to Approve+provision to Keycloak account + role
 *   + data scope + set-password email (Mailpit).
 *
 * Real PKCE login to the admin dashboard (privileged-ops realm) as an R-OPS-ADM
 * operator. The request is created through the anonymous public endpoint, which
 * also checks that fayda_id/verified_via are accepted on entry. Backend outcomes
 * are asserted from inside the cluster (kubectl exec).
 *
 * Run: NODE_PATH=<playwright-install>/node_modules node tests/test_access_provisioning.cjs
 */
const { chromium } = require('playwright');
const { execSync } = require('child_process');
const ADMIN = 'https://admin.127.0.0.1.nip.io/';
const PW = process.env.SEED_PASSWORD;
const EMAIL = 'e2e.provision@moh.gov.et';
const failures = [];
const check = (c, l) => { console.log((c ? '  ok   ' : '  FAIL ') + l); if (!c) failures.push(l); };

async function login(pg, user) {
  await pg.goto(ADMIN, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await pg.waitForSelector('#username', { timeout: 30000 });
  await pg.fill('#username', user); await pg.fill('#password', PW);
  await pg.click('#kc-login');
  await pg.waitForFunction(() => !document.querySelector('#username') && !!window.__nhdwAccessToken, { timeout: 60000 });
  await pg.waitForTimeout(2500);
}

function kube(pyBody) {
  const cmd =
    "kubectl -n nhdw exec -i $(kubectl -n nhdw get pods -l app=nhdw-gateway --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}') " +
    "-- env AP=$(kubectl -n nhdw get secret keycloak-secrets -o jsonpath='{.data.KC_BOOTSTRAP_ADMIN_PASSWORD}' | base64 -d) python3 -c \"" + pyBody + "\"";
  return execSync(cmd, { encoding: 'utf8', shell: '/bin/bash' }).trim();
}
function psql(sql) {
  const cmd =
    "kubectl -n nhdw exec -i postgres-vault-0 -- env PGPASSWORD=$(kubectl -n nhdw get secret postgres-vault-secrets -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d) " +
    "psql -U vault_admin -d nhdw_ops -tAc \"" + sql + "\"";
  return execSync(cmd, { encoding: 'utf8', shell: '/bin/bash' }).trim();
}

async function cleanup() {
  // KC user
  try {
    kube("import os,httpx;KC='http://keycloak.nhdw.svc.cluster.local:8080';R='moh-staff';" +
      "tok=httpx.post(f'{KC}/realms/master/protocol/openid-connect/token',data={'grant_type':'password','client_id':'admin-cli','username':'keycloak_admin','password':os.environ['AP']},timeout=15).json()['access_token'];" +
      "h={'Authorization':'Bearer '+tok};" +
      "us=httpx.get(f'{KC}/admin/realms/{R}/users',headers=h,params={'email':'" + EMAIL + "','exact':'true'},timeout=15).json();" +
      "[httpx.delete(f'{KC}/admin/realms/{R}/users/'+u['id'],headers=h,timeout=15) for u in us]");
  } catch {}
  try {
    psql("DELETE FROM ops_perm.user_role WHERE kc_sub IN (SELECT kc_sub FROM ops_identity.user WHERE email='" + EMAIL + "');" +
      "DELETE FROM ops_identity.user_scope WHERE kc_sub IN (SELECT kc_sub FROM ops_identity.user WHERE email='" + EMAIL + "');" +
      "DELETE FROM ops_governance.access_request WHERE email='" + EMAIL + "';" +
      "DELETE FROM ops_identity.user WHERE email='" + EMAIL + "';");
  } catch {}
}

(async () => {
  await cleanup();   // fresh start
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1200 } });
  const pg = await ctx.newPage();
  await login(pg, 'hana.d');

  // 1. Public submit, including fayda_id/verified_via. The public endpoint
  // ignores auth, but the admin /gw proxy needs a caller, so the admin's bearer
  // is sent (the endpoint does not check it).
  const sub = await pg.evaluate(async (email) => {
    const r = await fetch('/gw/public/access-requests', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + window.__nhdwAccessToken },
      body: JSON.stringify({
        full_name: 'E2E Provision User', email,
        purpose: 'Automated end-to-end test of the access-request provisioning flow.',
        dashboards: ['internal-bi'], fayda_id: 'FIN-3025-E2E', verified_via: 'fayda',
      }),
    });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, EMAIL);
  check(sub.status === 200 && /^AR-\d{4}-\d{4}$/.test(sub.body?.tracking_id || ''),
    `public submit (with fayda): ${sub.status} tracking=${sub.body?.tracking_id}`);
  const tracking = sub.body?.tracking_id;

  // 2. Navigate to Access Requests; the pending row appears.
  await pg.click('text=Users and Access');   // access requests live on this page
  await pg.waitForTimeout(2500);
  const rowVisible = await pg.evaluate((code) => {
    const rows = Array.from(document.querySelectorAll('[data-testid="ar-pending-row"]'));
    return rows.some(r => r.innerText.includes(code));
  }, tracking);
  check(rowVisible, `pending queue shows the new request ${tracking}`);

  // 3. Open Review on that row (robust locator).
  await pg.locator('[data-testid="ar-pending-row"]', { hasText: tracking })
          .locator('[data-testid="ar-review-btn"]').click();
  await pg.waitForSelector('[data-testid="review-modal"]', { timeout: 10000 });

  // 4. Approve + role R-CONS-ANA + region OR to provision.
  await pg.click('[data-testid="decision-approved"]');
  await pg.waitForTimeout(400);
  await pg.selectOption('[data-testid="review-role"]', 'R-CONS-ANA');
  await pg.selectOption('[data-testid="review-region"]', 'OR');
  await pg.click('[data-testid="review-submit"]');

  // 5. Wait for provisioning to complete (modal closes + resolved row shows the pill).
  await pg.waitForSelector('[data-testid="review-modal"]', { state: 'detached', timeout: 30000 }).catch(() => {});
  await pg.waitForTimeout(2500);
  const resolved = await pg.evaluate((code) => {
    const rows = Array.from(document.querySelectorAll('[data-testid="ar-resolved-row"]'));
    const row = rows.find(r => r.innerText.includes(code));
    return { present: !!row, provisioned: row ? /Provisioned/.test(row.innerText) : false };
  }, tracking);
  check(resolved.present, `request moved to resolved (${tracking})`);
  check(resolved.provisioned, `resolved row shows the "Provisioned" account pill`);

  // 6. Backend: KC account + role + scope + set-password email, all coherent.
  const kcRoles = kube("import os,httpx;KC='http://keycloak.nhdw.svc.cluster.local:8080';R='moh-staff';" +
    "tok=httpx.post(f'{KC}/realms/master/protocol/openid-connect/token',data={'grant_type':'password','client_id':'admin-cli','username':'keycloak_admin','password':os.environ['AP']},timeout=15).json()['access_token'];" +
    "h={'Authorization':'Bearer '+tok};" +
    "uid=httpx.get(f'{KC}/admin/realms/{R}/users',headers=h,params={'email':'" + EMAIL + "','exact':'true'},timeout=15).json()[0]['id'];" +
    "rs=httpx.get(f'{KC}/admin/realms/{R}/users/{uid}/role-mappings/realm',headers=h,timeout=15).json();" +
    "print(','.join(sorted(x['name'] for x in rs)))");
  check(/R-CONS-ANA/.test(kcRoles), `Keycloak account has realm role R-CONS-ANA (${kcRoles})`);

  const rows = psql("WITH u AS (SELECT kc_sub FROM ops_identity.user WHERE email='" + EMAIL + "') " +
    "SELECT (SELECT count(*) FROM ops_perm.user_role ur JOIN ops_perm.role r ON r.id=ur.role_id WHERE ur.kc_sub=(SELECT kc_sub FROM u) AND r.code='R-CONS-ANA' AND ur.is_active) " +
    "|| '/' || (SELECT count(*) FROM ops_identity.user_scope WHERE kc_sub=(SELECT kc_sub FROM u) AND kind='geography' AND value='OR') " +
    "|| '/' || (SELECT provisioning_status FROM ops_governance.access_request WHERE email='" + EMAIL + "')");
  check(rows === '1/1/provisioned', `coherent ops rows: user_role/user_scope/status = ${rows} (expect 1/1/provisioned)`);

  const mail = kube("import httpx;m=httpx.get('http://mailpit.nhdw.svc.cluster.local:8025/api/v1/messages',timeout=10).json();" +
    "print(sum(1 for x in (m.get('messages') or []) for t in (x.get('To') or []) if t.get('Address')=='" + EMAIL + "'))");
  check(parseInt(mail, 10) >= 1, `set-password email delivered to applicant in Mailpit (count=${mail})`);

  await pg.close();
  await browser.close();
  await cleanup();
  if (failures.length) { console.log(`\nFAILED: ${failures.length}`); process.exit(1); }
  console.log('\nALL GREEN'); process.exit(0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
