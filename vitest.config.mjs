// Frontend unit-test harness (#201). The SPA had zero tests — the only gate
// was bundle drift — so the #193 (fabricated demo data) / #188 (decorative
// search box) bug class shipped uncaught. This runs pure frontend logic
// (formatters, language normalization, filters, payload mappers) in CI.
//
// JSX transform MUST mirror scripts/build-frontend.mjs exactly: classic
// runtime, React.createElement / React.Fragment. React + ReactDOM are CDN
// globals at runtime (esbuild IIFE leaves them undefined-at-build); the setup
// file stubs them so importing a .jsx module never ReferenceErrors. These
// tests exercise pure logic, not rendering, so the node environment is enough.
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
