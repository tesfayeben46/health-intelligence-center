// Live regression: full steward walkthrough as steward.ops (R-STEW). The nav
// is narrowed to the steward's grants, the MDM queue works, master detail answers
// "who is this?", and Column Governance renders both tabs.
const { chromium } = require('playwright');
const BASE = process.env.ADMIN_BASE || 'https://admin.127.0.0.1.nip.io';
const PW = process.env.SEED_PASSWORD;
(async () => {
  const b = await chromium.launch({ args: ['--no-sandbox', '--ignore-certificate-errors'] });
  const res = [];
  const ok = (n, c, d='') => res.push(`${c?'ok  ':'FAIL'} ${n}${c?'':'  ['+String(d).slice(0,220)+']'}`);
  const ctx = await b.newContext({ ignoreHTTPSErrors: true });
  const p = await ctx.newPage();
  const errs = []; p.on('pageerror', e => errs.push(String(e)));

  await p.goto(BASE, { waitUntil: 'networkidle', timeout: 60000 });
  await p.waitForSelector('#username', { timeout: 30000 });
  await p.fill('#username', 'steward.ops'); await p.fill('#password', PW); await p.click('#kc-login');
  await p.waitForLoadState('networkidle', { timeout: 60000 });
  await p.waitForTimeout(4500);

  const nav = await p.locator('aside').innerText().catch(()=>'');
  ok('sees MDM Stewardship', nav.includes('MDM Stewardship'), nav);
  ok('sees Column Governance', nav.includes('Column Governance'), nav);
  ok('nav narrowed: no Users and Access', !nav.includes('Users and Access'), nav);
  ok('nav narrowed: no Quality & lineage', !nav.includes('Quality & lineage'), nav);
  ok('nav narrowed: no Health & topology', !nav.includes('Health & topology'), nav);

  await p.click('aside a:has-text("MDM Stewardship")');
  await p.waitForTimeout(3000);
  let body = await p.locator('body').innerText();
  ok('queue shows entity toggle', body.includes('Facilities') && body.includes('Commodities'), body.slice(0,300));
  ok('real candidates', body.includes('FAC-'), body.slice(0,300));
  ok('sources compared side by side', body.includes('What each source reported'), body.slice(0,400));

  ok('steward does not see Approvals', !(await p.locator('nav.tab-nav').innerText()).includes('Approvals'));

  // a verdict is a proposal for a second person: open the confirm step, read it,
  // cancel: never send anything on live
  await p.click('button:text-is("Same facility")');
  await p.waitForTimeout(500);
  body = await p.locator('body').innerText();
  ok('verdict goes to a second person', body.includes('second person approves') && body.includes('Send for approval'), body.slice(0,400));
  await p.click('button:text-is("Cancel")');

  // master detail: open the suggested master's record (Explore)
  await p.click('button:text-is("Open record")');
  await p.waitForTimeout(3500);
  body = await p.locator('body').innerText();
  ok('master record opens', body.includes('Record history') && body.includes('Golden record'), body.slice(0,400));
  ok('master record shows identifiers', body.includes('Known as'), body.slice(0,600));
  ok('master record shows source members', body.includes('Sources'), body.slice(0,600));

  await p.click('aside a:has-text("Column Governance")');
  await p.waitForTimeout(3500);
  body = await p.locator('body').innerText();
  ok('classified tab renders', body.includes('Column governance'), body.slice(0,300));
  ok('shows seed-managed rows', body.includes('seed'), body.slice(0,400));
  await p.click('button:has-text("Review queue")');
  await p.waitForTimeout(4000);
  body = await p.locator('body').innerText();
  ok('review queue scanned live columns', /mart columns scanned|Nothing to review/.test(body), body.slice(0,400));

  ok('no page errors', errs.length === 0, errs.join(' | '));
  console.log(res.join('\n'));
  await b.close();
  process.exit(res.some(x=>x.startsWith('FAIL')) ? 1 : 0);
})().catch(e => { console.error('FATAL', e.message); process.exit(2); });
