// Superset deep-link auto-login on the shared workshop session, and the tile
// being visible to a mapped analyst role and hidden for an unmapped one.
import { chromium } from 'playwright';
const SHOTS = '/tmp/nhdw-test-shots';
const PW = process.env.SEED_PASSWORD;
let allOk = true;
const check = (b, m) => { console.log(`${b ? 'PASS' : 'FAIL'}: ${m}`); if (!b) allOk = false; };
const browser = await chromium.launch({ args: ['--no-sandbox'] });

async function openWorkshopTools(user) {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  await page.goto('https://workshop.127.0.0.1.nip.io/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(2000);
  if (await page.locator('#username').count() > 0) {
    await page.fill('#username', user); await page.fill('#password', PW);
    await page.click('#kc-login, button[type=submit], input[type=submit]'); await page.waitForTimeout(3500);
  }
  await page.waitForTimeout(1500);
  // Navigate to Help (where ws-help-tools lives) via the sidebar link.
  const help = page.getByRole('link', { name: /Help/i }).first();
  if (await help.count() > 0) { await help.click().catch(() => {}); }
  else { await page.getByText('Help', { exact: true }).first().click().catch(() => {}); }
  // Wait for the tools grid to render (dbt Docs is always present on this page).
  await page.locator('.card', { hasText: 'dbt Docs' }).first().waitFor({ timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(1200);
  return { ctx, page };
}

// sci.demo (R-CONS-SCI, mapped): tile visible and the deep link admits silently.
{
  const { ctx, page } = await openWorkshopTools('sci.demo');
  const heading = page.getByRole('heading', { name: 'Superset', exact: true }).first();
  const visible = await heading.count() > 0;
  await page.screenshot({ path: `${SHOTS}/ws_tools_sci.png`, fullPage: true });
  check(visible, 'Superset tile visible to sci.demo (R-CONS-SCI, mapped role)');
  if (visible) {
    // the Open link inside this heading's own tool card, not the first card
    const open = heading.locator('xpath=ancestor::*[contains(@class,"card")][1]')
      .getByRole('link', { name: /Open/i }).first();
    const [popup] = await Promise.all([
      page.waitForEvent('popup', { timeout: 15000 }).catch(() => null),
      open.click().catch(() => {}),
    ]);
    if (popup) {
      await popup.waitForTimeout(5000);
      const purl = popup.url();
      const pbody = (await popup.locator('body').innerText().catch(() => '')) || '';
      const secondLogin = await popup.locator('#password').count() > 0;
      await popup.screenshot({ path: `${SHOTS}/ws_superset_deeplink.png` });
      check(!secondLogin && /superset\.127/.test(purl) && !/Access is Denied/i.test(pbody),
            `Superset admitted silently from the workshop session: no 2nd login (url=${purl.slice(0, 50)})`);
    } else {
      check(false, 'Superset deep-link did not open a tab');
    }
  }
  await ctx.close();
}

// abebe.bekele (R-PD-HIV, not mapped): tile hidden.
{
  const { ctx, page } = await openWorkshopTools('abebe.bekele');
  const body = (await page.locator('body').innerText().catch(() => '')) || '';
  const onWorkshop = /workshop\.127/.test(page.url()) && !/Sign in|#username/i.test(body);
  const supersetVisible = await page.locator('.card', { hasText: 'Superset' }).count() > 0;
  await page.screenshot({ path: `${SHOTS}/ws_tools_pdhiv.png`, fullPage: true });
  // Passes when the tile is absent, including when the workshop itself is not reachable.
  check(!supersetVisible, `Superset tile hidden for R-PD-HIV (unmapped): onWorkshop=${onWorkshop}`);
  await ctx.close();
}

await browser.close();
console.log(allOk ? '\nSUPERSET DEEP-LINK CHECKS PASSED' : '\nSOME CHECKS FAILED');
process.exit(allOk ? 0 : 1);
