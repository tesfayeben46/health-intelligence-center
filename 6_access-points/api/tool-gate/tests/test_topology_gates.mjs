// Verify the live topology: the four tool-gates appear in the security zone and
// there is no oauth2-proxy node. Captures the graph the admin SPA fetches from
// /gw/topology/graph.
import { chromium } from 'playwright';
const SHOTS = '/tmp/nhdw-test-shots';
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1500, height: 950 } });
const page = await ctx.newPage();
let graph = null;
page.on('response', async r => {
  if (/\/gw\/topology\/graph/.test(r.url()) && r.status() === 200) {
    try { graph = await r.json(); } catch {}
  }
});
await page.goto('https://admin.127.0.0.1.nip.io/', { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(2000);
if (await page.locator('#username').count() > 0) {
  await page.fill('#username', 'solomon.m'); await page.fill('#password', process.env.SEED_PASSWORD);
  await page.click('#kc-login, button[type=submit], input[type=submit]'); await page.waitForTimeout(4000);
}
await page.waitForTimeout(3500);
await page.screenshot({ path: `${SHOTS}/topology_after.png`, fullPage: false });
await browser.close();

let allOk = true;
const check = (b, m) => { console.log(`${b ? 'PASS' : 'FAIL'}: ${m}`); if (!b) allOk = false; };
if (!graph) { console.log('FAIL: never captured the topology graph payload'); process.exit(1); }
const nodes = graph.nodes || [];
const byId = {}; nodes.forEach(n => { byId[n.id || n.name] = n; });
const names = nodes.map(n => n.id || n.name);
console.log('  gate nodes:', names.filter(n => /gate/.test(n)).join(', ') || '(none)');
const GATES = ['mlflow-gate', 'prometheus-gate', 'alertmanager-gate', 'headlamp-gate'];
for (const g of GATES) {
  const n = byId[g];
  const zone = n && (n.zone || n.placement);
  check(!!n, `topology has node ${g}${n ? ` (zone=${zone})` : ''}`);
}
check(!names.includes('oauth2-proxy'), 'oauth2-proxy is GONE from the live topology');
const gateZones = GATES.map(g => byId[g]?.zone).filter(Boolean);
check(gateZones.every(z => z === 'security'), `all gates in the security zone (${[...new Set(gateZones)].join(',')})`);
console.log(allOk ? '\nTOPOLOGY CHECKS PASSED' : '\nSOME CHECKS FAILED');
process.exit(allOk ? 0 : 1);
