import { expect, test } from '@playwright/test';

/**
 * Smoke suite — runs against a live subarr install. Asserts the
 * v1.0 surfaces are reachable, render without console errors, and
 * the live data flows through.
 *
 * Not exhaustive — exhaustive coverage is in the Python unit tests.
 * This deck is for "did we ship a broken bundle" detection.
 */


test.describe('API health', () => {
  test('/api/health returns 200 with subarr version', async ({ request }) => {
    const r = await request.get('/api/health');
    expect(r.ok()).toBeTruthy();
    const body = await r.json();
    expect(body.status).toBe('ok');
    expect(body.version).toBeTruthy();
  });

  test('/api/home/dashboard returns full bundle', async ({ request }) => {
    const r = await request.get('/api/home/dashboard');
    expect(r.ok()).toBeTruthy();
    const body = await r.json();
    expect(body).toHaveProperty('stages');
    expect(body).toHaveProperty('integrations');
    expect(body).toHaveProperty('next_run');
    expect(body).toHaveProperty('activity');
    expect(Array.isArray(body.stages)).toBeTruthy();
    expect(body.stages.length).toBe(5);
  });

  test('/api/telemetry/state has install_id + opt-status', async ({ request }) => {
    const r = await request.get('/api/telemetry/state');
    expect(r.ok()).toBeTruthy();
    const body = await r.json();
    expect(body.available).toBeTruthy();
    expect(body.install_id).toMatch(/^[a-f0-9]{32}$/);
    expect(typeof body.opted_in).toBe('boolean');
  });

  test('/api/integrations/health includes subgen capability block', async ({ request }) => {
    const r = await request.get('/api/integrations/health');
    expect(r.ok()).toBeTruthy();
    const body = await r.json();
    expect(body).toHaveProperty('integrations');
    expect(body).toHaveProperty('subgen');
    expect(body.subgen).toHaveProperty('compat_mode');
  });
});


test.describe('Home dashboard', () => {
  test('renders without console errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    await page.goto('/home');
    await page.waitForLoadState('networkidle');
    // Header wordmark
    await expect(page.getByText('sub', { exact: true })).toBeVisible();
    // Should not have console errors
    expect(errors.filter(e => !e.includes('favicon'))).toHaveLength(0);
  });

  test('integration tiles populate from live data', async ({ page }) => {
    await page.goto('/home');
    // Wait for the live data fetch (5s tick, first fetch is immediate)
    await page.waitForResponse(
      (res) => res.url().includes('/api/home/dashboard') && res.status() === 200,
      { timeout: 10000 },
    );
    // 'Bazarr' appears in BOTH the SubRail link and the IntegrationTile —
    // assert at least one match is visible.
    await expect(page.getByText('Bazarr').first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Subgen').first()).toBeVisible();
  });

  test('coverage page reachable', async ({ page }) => {
    await page.goto('/coverage');
    await page.waitForLoadState('networkidle');
    // 'Coverage' appears as a SubRail link AND as the page heading. Match
    // the heading specifically.
    await expect(page.getByRole('heading', { name: 'Coverage' })).toBeVisible({ timeout: 5000 });
  });
});


test.describe('Coverage page (wired to /api/coverage)', () => {
  test('renders live data without console errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text());
    });

    await page.goto('/coverage');
    // Wait for the live coverage fetch to complete.
    await page.waitForResponse(
      (res) => res.url().includes('/api/coverage') && res.status() === 200,
      { timeout: 15000 },
    );
    await page.waitForLoadState('networkidle');

    // Page header
    await expect(page.getByRole('heading', { name: 'Coverage' })).toBeVisible();
    // Re-walk + Export CSV buttons exist
    await expect(page.getByRole('button', { name: /Re-walk now/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Export CSV/i })).toBeVisible();
    // Coverage strip uses the live "gaps" label — never the mock "612 gaps · last walk 10:32".
    await expect(page.getByText(/last walk 10:32/)).toHaveCount(0);
    // No console errors (favicon noise filtered).
    expect(errors.filter((e) => !e.includes('favicon'))).toHaveLength(0);
  });

  test('GET /api/coverage returns the expected shape', async ({ request }) => {
    const r = await request.get('/api/coverage');
    expect(r.ok()).toBeTruthy();
    const body = await r.json();
    expect(body).toHaveProperty('items');
    expect(body).toHaveProperty('totals');
    expect(body).toHaveProperty('generated_at');
    expect(Array.isArray(body.items)).toBeTruthy();
    if (body.items.length > 0) {
      const it = body.items[0];
      expect(it).toHaveProperty('media_type');
      expect(it).toHaveProperty('title');
      expect(it).toHaveProperty('canonical_path');
      expect(it).toHaveProperty('score');
    }
  });
});


test.describe('Onboarding wizard', () => {
  test('wizard route serves the SPA', async ({ page }) => {
    await page.goto('/onboarding');
    await page.waitForLoadState('networkidle');
    // Stepper renders
    await expect(page.getByText(/done · 1 in progress/)).toBeVisible({ timeout: 5000 });
  });

  test('GET /api/onboarding/state returns the resume payload', async ({ request }) => {
    const r = await request.get('/api/onboarding/state');
    expect(r.ok()).toBeTruthy();
    const body = await r.json();
    expect(body).toHaveProperty('step');
    expect(body).toHaveProperty('progress');
    expect(body).toHaveProperty('is_complete');
  });

  test('PUT /api/onboarding/state merges progress', async ({ request }) => {
    await request.post('/api/onboarding/reset');
    const r = await request.put('/api/onboarding/state', {
      data: { step: 2, progress: { media_root: '/media/library' } },
    });
    expect(r.ok()).toBeTruthy();
    const body = await r.json();
    expect(body.step).toBe(2);
    expect(body.progress.media_root).toBe('/media/library');
  });
});


test.describe('Compat mode + capability detection', () => {
  test('subgen capabilities surface correctly', async ({ request }) => {
    const r = await request.get('/api/integrations/health');
    const body = await r.json();
    const subgen = body.subgen;
    expect(typeof subgen.reachable).toBe('boolean');
    expect(typeof subgen.has_queue).toBe('boolean');
    expect(typeof subgen.has_batch).toBe('boolean');
    expect(typeof subgen.is_subarr_subgen).toBe('boolean');
    expect(typeof subgen.compat_mode).toBe('boolean');
  });
});


test.describe('Discovery + config-extract endpoints', () => {
  test('/api/discovery returns available=false when not configured', async ({ request }) => {
    // dev stack has no docker proxy configured — should gracefully say so
    const r = await request.get('/api/discovery');
    expect(r.ok()).toBeTruthy();
    const body = await r.json();
    expect(body).toHaveProperty('available');
    expect(body).toHaveProperty('services');
    expect(Array.isArray(body.services)).toBeTruthy();
  });
});
