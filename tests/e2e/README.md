# Subarr E2E (Playwright)

Smoke suite against a running subarr install. Not exhaustive — Python
unit tests cover internals. This is the "did we ship a broken bundle?"
gate.

## One-time setup

```bash
cd tests/e2e
npm install                          # ~2-3 min, installs @playwright/test
npx playwright install chromium      # ~1 min, downloads the browser
```

## Run against the dev stack

```bash
# Default: http://localhost:9923 (subarr-next dev stack)
npm test

# Override:
SUBARR_BASE_URL=http://localhost:9922 npm test
```

## Interactive UI mode (recommended for development)

```bash
npm run test:ui
```

## After failures: view the HTML report

```bash
npm run report
```

Failures auto-capture screenshots + video at
`playwright-report/data/` for triage.

## What's covered

- **API health**: /api/health, /api/home/dashboard, /api/telemetry/state,
  /api/integrations/health
- **Home dashboard**: renders without console errors, integration tiles
  populate from live data, coverage page reachable
- **Onboarding wizard**: route serves, state endpoint round-trip,
  progress merging
- **Compat mode**: subgen capability detection surfaces all fields
- **Discovery**: graceful "not configured" response on dev stack

## Skipped scenarios (out of scope for smoke)

- Auth flow (separate suite for the basic-auth middleware)
- File-upload paths (no upload UX in v1.0)
- Long-running scans (would need a stub subgen with hardware-fast turnaround)
- Multi-user / multi-instance (subarr is single-tenant by design)

## CI integration

When the security CI workflow runs against a deployed image, add:

```yaml
- name: e2e smoke
  working-directory: tests/e2e
  run: |
    npm ci
    npx playwright install --with-deps chromium
    SUBARR_BASE_URL=http://localhost:9923 npx playwright test
```

The release.yml workflow doesn't currently gate on this — once the
suite has run cleanly a few times in CI we add it to the release
gate.
