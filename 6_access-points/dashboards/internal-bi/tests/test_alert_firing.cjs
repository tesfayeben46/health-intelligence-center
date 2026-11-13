/**
 * Alert firing, the full loop end to end:
 *   subscribe, evaluate (the ml_predictions alarm signal), notify (bell +
 *   NotificationPanel), email (Mailpit), then mark-read clears the badge.
 *
 * The alarm signal comes from the seeded data: Malaria in Central Ethiopia sits at
 * alarm_prob 1.00 in the latest forecast week, so a 0.70 subscription must fire. Evaluation is
 * triggered exactly as the alert_evaluation Airflow DAG does: POST
 * /api/alerts/evaluate with the service token: via kubectl exec (the browser
 * can't hold the service secret). Owner-scoped throughout.
 *
 * Run: NODE_PATH=<playwright-install>/node_modules node tests/test_alert_firing.cjs
 */
const { chromium } = require('playwright');
const { execSync } = require('child_process');
const BI = 'https://internal-bi.127.0.0.1.nip.io/';
const PW = process.env.SEED_PASSWORD;
const failures = [];
const check = (c, l) => { console.log((c ? '  ok   ' : '  FAIL ') + l); if (!c) failures.push(l); };

async function login(pg, user) {
  await pg.goto(BI, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await pg.waitForSelector('#username', { timeout: 30000 });
  await pg.fill('#username', user); await pg.fill('#password', PW);
  await pg.click('#kc-login');
  await pg.waitForFunction(() => !document.querySelector('#username') && !!window.__nhdwAccessToken, { timeout: 60000 });
  await pg.waitForTimeout(2000);
}
const api = (pg, method, path, body) => pg.evaluate(async ([m, p, b]) => {
  const r = await fetch('/gw/alerts' + p, {
    method: m, headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + window.__nhdwAccessToken },
    body: b ? JSON.stringify(b) : undefined,
  });
  let j = null; try { j = await r.json(); } catch {}
  return { status: r.status, body: j };
}, [method, path, body]);

// Trigger the evaluator like the Airflow DAG: service token, no bearer.
function evaluateViaService() {
  const cmd =
    "kubectl -n nhdw exec -i " +
    "$(kubectl -n nhdw get pods -l app=nhdw-gateway --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}') " +
    "-- env TOKEN=$(kubectl -n nhdw get secret alert-eval-secrets -o jsonpath='{.data.TOKEN}' | base64 -d) " +
    "python3 -c \"import os,httpx;print(httpx.post('http://localhost:8000/api/alerts/evaluate',headers={'X-Eval-Token':os.environ['TOKEN']},timeout=90).text)\"";
  return execSync(cmd, { encoding: 'utf8', shell: '/bin/bash' }).trim();
}
// Count Mailpit messages addressed to a recipient.
function mailpitCountTo(addr) {
  const cmd =
    "kubectl -n nhdw exec -i " +
    "$(kubectl -n nhdw get pods -l app=nhdw-gateway --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}') " +
    "-- python3 -c \"import httpx;m=httpx.get('http://mailpit.nhdw.svc.cluster.local:8025/api/v1/messages',timeout=10).json();" +
    "print(sum(1 for x in (m.get('messages') or []) for t in (x.get('To') or []) if t.get('Address')=='" + addr + "'))\"";
  return parseInt(execSync(cmd, { encoding: 'utf8', shell: '/bin/bash' }).trim() || '0', 10);
}

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1500, height: 1200 } });
  const pg = await ctx.newPage();
  await login(pg, 'tadesse.h');

  // Clean slate: delete tadesse's subs (CASCADE removes prior notifications).
  const existing = await api(pg, 'GET', '/subscriptions');
  for (const s of (existing.body || [])) await api(pg, 'DELETE', '/subscriptions/' + s.id);
  await pg.evaluate(() => window.__biApp.loadAlertNotifications());
  await pg.waitForTimeout(800);
  let badge0 = await pg.evaluate(() => { const b = document.querySelector('.tb-badge'); return b ? b.innerText.trim() : ''; });
  check(badge0 === '', `clean slate: no unread badge (badge="${badge0}")`);
  const mailBefore = mailpitCountTo('tadesse.h@moh.gov.et');

  // 1. Subscribe to a scope that will fire (Malaria / Central Ethiopia / >=0.70).
  const created = await api(pg, 'POST', '/subscriptions', { disease: 'malaria', geography: 'Central Ethiopia', threshold: 0.7, cadence: 'realtime' });
  check(created.status === 200 && created.body?.id, `subscribe Malaria/Central Ethiopia/0.70: 200`);

  // 2. Evaluate (as the DAG). The real alarm signal must fire at least once.
  const evalOut = evaluateViaService();
  console.log('    evaluate:', evalOut);
  let fired = 0; try { fired = JSON.parse(evalOut).notifications_fired; } catch {}
  check(fired >= 1, `evaluate fired a notification (notifications_fired=${fired})`);

  // 3. Email landed in Mailpit for tadesse.
  const mailAfter = mailpitCountTo('tadesse.h@moh.gov.et');
  check(mailAfter > mailBefore, `alert email delivered to Mailpit (before=${mailBefore} after=${mailAfter})`);

  // 4. Bell badge reflects the unread notification (refresh cache as the app does).
  await pg.evaluate(() => window.__biApp.loadAlertNotifications());
  await pg.waitForTimeout(1000);
  const badge1 = await pg.evaluate(() => { const b = document.querySelector('.tb-badge'); return b ? b.innerText.trim() : ''; });
  check(badge1 === '1', `topbar bell badge shows unread count (badge="${badge1}")`);

  // 5. Open the NotificationPanel via the bell; the fired alert is listed.
  await pg.click('.tb-iconbtn[title="Alert notifications"]');
  await pg.waitForSelector('[data-testid="notif-panel"]', { timeout: 10000 });
  await pg.waitForTimeout(800);
  const panel = await pg.evaluate(() => {
    const rows = Array.from(document.querySelectorAll('[data-testid="notif-row"]'));
    return { count: rows.length, text: rows.map(r => r.innerText).join(' || ') };
  });
  check(panel.count >= 1, `NotificationPanel lists the fired alert (rows=${panel.count})`);
  check(/malaria/i.test(panel.text) && /central ethiopia/i.test(panel.text), `notification names the disease + region`);
  check(/100%/.test(panel.text) || /reached/i.test(panel.text), `notification carries the alarm message`);

  // 6. Mark read (click the row); the badge clears.
  await pg.click('[data-testid="notif-row"]');
  await pg.waitForTimeout(1200);
  const badge2 = await pg.evaluate(() => { const b = document.querySelector('.tb-badge'); return b ? b.innerText.trim() : ''; });
  check(badge2 === '', `mark-read clears the bell badge (badge="${badge2}")`);

  // 7. "Manage subscriptions" opens the AlertModal (subscription CRUD surface).
  await pg.click('[data-testid="notif-manage"]');
  await pg.waitForTimeout(800);
  const modalOpen = await pg.evaluate(() => !!document.querySelector('.modal-hdr h2') &&
    /subscription/i.test(document.querySelector('.modal-hdr h2').innerText));
  check(modalOpen, `"Manage subscriptions" opens the AlertModal`);

  // Cleanup: remove the test subscription (CASCADE clears the notification).
  const subs = await api(pg, 'GET', '/subscriptions');
  for (const s of (subs.body || [])) await api(pg, 'DELETE', '/subscriptions/' + s.id);

  await pg.close();
  await browser.close();
  if (failures.length) { console.log(`\nFAILED: ${failures.length}`); process.exit(1); }
  console.log('\nALL GREEN'); process.exit(0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
