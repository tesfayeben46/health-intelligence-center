// Visual-fill audit: role-absent widgets must not leave grid holes or empty
// section shells. Full-page screenshots + programmatic grid checks for
// abebe.bekele (R-PD-HIV, sparse Resource) and kebede.oromia (partial rows).
import { chromium } from 'playwright';
const SHOTS = '/tmp/nhdw-test-shots';
let allOk = true;
const check = (b, m) => { console.log(`${b ? 'PASS' : 'FAIL'}: ${m}`); if (!b) allOk = false; };
const browser = await chromium.launch({ args: ['--no-sandbox'] });

async function audit(user, pageLabel, tag) {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto('https://internal-bi.127.0.0.1.nip.io/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('#username', { timeout: 20000 });
  await page.fill('#username', user);
  await page.fill('#password', process.env.SEED_PASSWORD);
  await page.press('#password', 'Enter');
  await page.waitForLoadState('networkidle', { timeout: 40000 }).catch(() => {});
  for (let i = 0; i < 3; i++) {
    const retry = page.locator('button, a').filter({ hasText: /try again/i }).first();
    if (!(await retry.count())) break;
    await retry.click();
    await page.waitForLoadState('networkidle', { timeout: 40000 }).catch(() => {});
  }
  await page.waitForTimeout(1500);
  await page.locator('nav, aside, [class*=side], [class*=nav]').getByText(pageLabel, { exact: false }).first().click();
  await page.waitForTimeout(4000);
  for (let y = 0; y < 8; y++) { await page.mouse.wheel(0, 1000); await page.waitForTimeout(300); }
  await page.waitForTimeout(2000);

  // Programmatic grid audit: per section grid, every ROW of visible cards
  // should sum to the full column count (last row may under-fill only if the
  // section legitimately has a trailing partial row: packRows fills those
  // too, so we assert full rows). Also: no section header without any card.
  const audit = await page.evaluate(() => {
    const out = { sections: 0, emptySections: [], underfilledRows: [], holes: 0 };
    document.querySelectorAll('section, [class*=section]').forEach(sec => {
      const grid = sec.querySelector('[style*="grid"], [class*=grid]');
      const title = (sec.querySelector('h2,h3,[class*=title]')?.textContent || '').trim().slice(0, 40);
      if (!grid || !title) return;
      const cards = [...grid.children].filter(c => getComputedStyle(c).display !== 'none');
      out.sections++;
      if (!cards.length) { out.emptySections.push(title); return; }
      // sum spans per visual row using offsetTop clustering
      const rows = new Map();
      for (const c of cards) {
        const top = Math.round(c.getBoundingClientRect().top / 40) * 40;
        const span = parseInt((c.style.gridColumn.match(/span (\d+)/) || [])[1] || '0', 10);
        rows.set(top, (rows.get(top) || 0) + span);
      }
      for (const [, sum] of rows) if (sum > 0 && sum < 12) out.underfilledRows.push(`${title}=${sum}`);
    });
    return out;
  });
  console.log(`INFO ${tag}: sections=${audit.sections} empty=[${audit.emptySections.join('; ')}] underfilled=[${audit.underfilledRows.join('; ')}]`);
  check(audit.emptySections.length === 0, `${tag}: no empty section shells`);
  check(audit.underfilledRows.length === 0, `${tag}: every visible row fills 12 tracks`);
  await page.screenshot({ path: `${SHOTS}/reflow_${tag}.png`, fullPage: true });
  await ctx.close();
}

try {
  await audit('abebe.bekele', 'Resource', 'hiv_resource');
  await audit('kebede.oromia', 'Resource', 'oromia_resource');
  // Overview is CORRECTLY elided for R-PD-HIV (no overview placements):
  // audit the fullest page instead (analyst) plus a scoped Service page.
  await audit('analyst.ana', 'Overview', 'analyst_overview');
  await audit('abebe.bekele', 'Service', 'hiv_service');
} catch (e) { console.log('FAIL: exception: ' + e.message); allOk = false; }
await browser.close();
console.log(allOk ? 'ALL OK' : 'SOME CHECKS FAILED');
process.exit(allOk ? 0 : 1);
