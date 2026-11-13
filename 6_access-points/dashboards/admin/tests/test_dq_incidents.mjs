// Live regression: the admin DQ page renders the incidents section and
// governance KPIs over real detector findings (root / R-SUPER).
const { chromium } = require('playwright');
const BASE = process.env.ADMIN_BASE || 'https://admin.127.0.0.1.nip.io';
const PW = process.env.SEED_PASSWORD;
(async () => {
  const b = await chromium.launch({ args: ['--no-sandbox', '--ignore-certificate-errors'] });
  const res = []; const ok = (n,c,d='') => res.push(`${c?'ok  ':'FAIL'} ${n}${c?'':'  ['+String(d).slice(0,250)+']'}`);
  const ctx = await b.newContext({ ignoreHTTPSErrors: true });
  const p = await ctx.newPage(); const errs=[]; p.on('pageerror', e=>errs.push(String(e)));
  await p.goto(BASE, { waitUntil:'networkidle', timeout:60000 });
  await p.waitForSelector('#username',{timeout:30000});
  await p.fill('#username','root'); await p.fill('#password',PW); await p.click('#kc-login');
  await p.waitForLoadState('networkidle',{timeout:60000}); await p.waitForTimeout(4500);

  await p.click('aside a:has-text("Quality & lineage")');
  await p.waitForTimeout(6000);
  const body = await p.locator('body').innerText();
  ok('DQ page renders', body.length > 200, body.slice(0,200));
  ok('incidents section present', /incident/i.test(body), body.slice(0,600));
  ok('shows the real stale_ingest findings', /stale_ingest|Stale ingest/i.test(body), body.slice(0,900));
  ok('shows the MDM backlog finding', /mdm_backlog|MDM backlog/i.test(body), body.slice(0,900));
  ok('no page errors', errs.length===0, errs.join(' | '));
  console.log(res.join('\n'));
  await b.close();
  process.exit(res.some(x=>x.startsWith('FAIL'))?1:0);
})().catch(e=>{console.error('FATAL',e.message);process.exit(2);});
