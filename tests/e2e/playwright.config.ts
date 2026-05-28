import { defineConfig, devices } from '@playwright/test';

/**
 * Subarr v1.0 E2E config.
 *
 * Targets a running subarr instance — does NOT spin one up itself.
 * Set SUBARR_BASE_URL to override the default dev-stack port.
 */
export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],

  use: {
    baseURL: process.env.SUBARR_BASE_URL || 'http://localhost:9923',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
  ],
});
