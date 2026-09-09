// Frontend unit-test harness (#201). The SPA had zero tests — the only gate
// was bundle drift — so the #193 (fabricated demo data) / #188 (decorative
// search box) bug class shipped uncaught. This runs pure frontend logic
// (formatters, language normalization, filters, payload mappers) in CI.
//
// JSX transform MUST mirror scripts/build-frontend.mjs exactly: classic
// runtime, React.createElement / React.Fragment. React + ReactDOM are runtime
// GLOBALS (esbuild IIFE leaves them undefined-at-build) served from
// static/v1/vendor/ -- they are VENDORED IN THIS REPO, not fetched from a CDN,
// which this comment used to claim. The setup file stubs them so importing a
// .jsx module never ReferenceErrors.
//
// Default environment is node because most tests exercise pure logic. The
// component-level tests added for #517 opt into jsdom per file with a
// `@vitest-environment jsdom` docblock and load the VENDORED React, so the
// React under test is byte-identical to the one shipped to browsers rather
// than a second copy pulled from npm that could drift from it.
import { defineConfig } from 'vitest/config';

export default defineConfig({
  // Vitest 4 transforms with oxc, not esbuild. Force the CLASSIC JSX runtime
  // (React.createElement / React.Fragment) to match build-frontend.mjs —
  // otherwise oxc defaults to the automatic runtime and injects a
  // `react/jsx-dev-runtime` import we don't ship (React is a CDN global).
  oxc: {
    jsx: {
      runtime: 'classic',
      pragma: 'React.createElement',
      pragmaFrag: 'React.Fragment',
    },
  },
  test: {
    environment: 'node',
    include: ['src/subarr/static/v1/**/*.test.{js,jsx}'],
    setupFiles: ['scripts/vitest.setup.js'],
  },
});
