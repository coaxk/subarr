/**
 * README screenshot capture — full-page, product-only (no browser chrome),
 * every guidance/explainer panel expanded. Output → docs/screenshots/.
 *
 * Run against the live dev stack:
 *   SUBARR_BASE_URL=http://localhost:9923 npx playwright test screenshots.spec.ts
 *
 * Not part of CI — this is a tooling spec for generating README assets.
 */
import { test, expect } from '@playwright/test';
import * as path from 'path';

const OUT = path.resolve(__dirname, '..', '..', '..', 'docs', 'screenshots');

// Click every collapsed explainer/guidance toggle on the page. Each entry
// is matched as visible text; absent ones are skipped silently so the same
// helper is safe on any page.
async function expandGuidance(page) {
  const toggles = [
    /How is this list built/i,
    /How it works/i,
    /How calibrated/i,
    /Why am I seeing this/i,
    /What is this\??/i,
    /Show details/i,
    /Learn more/i,
  ];
  for (const re of toggles) {
    const loc = page.getByText(re);
    const n = await loc.count().catch(() => 0);
    for (let i = 0; i < n; i++) {
      try { await loc.nth(i).click({ timeout: 800 }); } catch { /* not a toggle */ }
    }
  }
  // Collapsed "show" affordances on the probe-gate buckets.
  const show = page.getByText(/^show$/i);
  const ns = await show.count().catch(() => 0);
  for (let i = 0; i < ns; i++) {
    try { await show.nth(i).click({ timeout: 800 }); } catch { /* */ }
  }
  await page.waitForTimeout(400);
}

async function shoot(page, name: string) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
}

test.use({ viewport: { width: 1440, height: 1000 } });

test('01 dashboard', async ({ page }) => {
  await page.goto('/home');
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(2500);
  await expandGuidance(page);
  await shoot(page, '01-dashboard');
});

test('02 coverage (probe-gate gap list)', async ({ page }) => {
  await page.goto('/coverage');
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(3500);
  await expandGuidance(page);
  // Flat mode so the scored gap rows are visible in the shot.
  try { await page.getByRole('button', { name: 'Flat' }).click({ timeout: 1500 }); } catch {}
  await page.waitForTimeout(800);
  await shoot(page, '02-coverage');
});

test('03 queue (bulk-select)', async ({ page }) => {
  await page.goto('/queue');
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(2500);
  await expandGuidance(page);
  // Tick a couple of checkboxes to reveal the bulk-action bar.
  const checks = page.locator('input[type=checkbox]');
  const cn = await checks.count().catch(() => 0);
  for (let i = 0; i < Math.min(2, cn); i++) {
    try { await checks.nth(i).check({ timeout: 800 }); } catch {}
  }
  await page.waitForTimeout(500);
  await shoot(page, '03-queue');
});

test('04 library (probe cache)', async ({ page }) => {
  await page.goto('/library');
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(2500);
  await expandGuidance(page);
  await shoot(page, '04-library');
});

test('05 review (audio-language)', async ({ page }) => {
  await page.goto('/review');
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(2500);
  await expandGuidance(page);
  await shoot(page, '05-review');
});

test('06 rules (auto-queue)', async ({ page }) => {
  await page.goto('/rules');
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(2500);
  await expandGuidance(page);
  await shoot(page, '06-rules');
});

test('07 settings (integrations)', async ({ page }) => {
  await page.goto('/settings');
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(2500);
  await expandGuidance(page);
  await shoot(page, '07-settings-integrations');
});

test('08 settings (telemetry)', async ({ page }) => {
  await page.goto('/settings');
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(2000);
  try { await page.getByRole('button', { name: /^Telemetry$/ }).click({ timeout: 2000 }); } catch {}
  await page.waitForTimeout(1200);
  await expandGuidance(page);
  await shoot(page, '08-settings-telemetry');
});

test('09 logs', async ({ page }) => {
  await page.goto('/logs');
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(2500);
  await expandGuidance(page);
  await shoot(page, '09-logs');
});
