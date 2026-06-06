// Build the v1 static SPA bundles with esbuild.
//
// Why: the v1 SPA originally shipped React + Babel-standalone as CDN scripts
// and ran a JSX → JS transform in the browser on every page load. That
// recompile stalled the Chrome renderer on conditional-render swaps in the
// onboarding wizard (the freeze surfaced during the v1.0 manual E2E).
//
// Now: each HTML page loads exactly one pre-built IIFE bundle (compiled
// here). React and ReactDOM still come from the CDN as before — esbuild's
// IIFE format leaves undefined identifiers (`React`, `ReactDOM`) as runtime
// global refs, and the CDN scripts load first via plain `<script>` tags,
// so `React.createElement(...)` just works.
//
// Outputs `<page>.bundle.js` + `<page>.bundle.js.map` next to the JSX
// sources under static/v1/home-hifi/. Bundles are committed (gitignored
// would force every consumer to install Node — we want `pip install`
// to ship a working SPA out of the box).
//
// Drift check: `npm run check:frontend` rebuilds and fails if anything
// changed — the CI job runs this on every PR so JSX edits without a
// rebuild can't slip in.

import { context, build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');
const ENTRIES_DIR = resolve(ROOT, 'src/subarr/static/v1/home-hifi/entries');
const OUTDIR = resolve(ROOT, 'src/subarr/static/v1/home-hifi');

// One IIFE bundle per HTML page. Entry file name → output file name.
const PAGES = [
  'onboarding',
  'home',
  'coverage',
  'rules',
  'settings',
  'file-modal',
  'queue',
  'library',
  'logs',
  'review',
  'arena',
];

const watch = process.argv.includes('--watch');

/** @type {import('esbuild').BuildOptions} */
const baseOptions = {
  entryPoints: Object.fromEntries(
    PAGES.map((p) => [`${p}.bundle`, resolve(ENTRIES_DIR, `${p}.entry.jsx`)]),
  ),
  outdir: OUTDIR,
  bundle: true,
  format: 'iife',
  target: ['es2020'],
  jsx: 'transform',
  jsxFactory: 'React.createElement',
  jsxFragment: 'React.Fragment',
  sourcemap: true,
  // React + ReactDOM stay on window via the CDN <script> tags. esbuild's
  // IIFE format leaves unresolved identifiers as runtime globals, so
  // we just don't import them. JSX compiles to `React.createElement(...)`
  // which finds the CDN copy at runtime.
  loader: { '.jsx': 'jsx' },
  logLevel: 'info',
  // Production bundles are minified; sourcemaps cover the readability
  // gap. Negligible bandwidth in a homelab context but worth keeping
  // gzip-friendly.
  minify: !watch,
  // Drop the leading shebang-style banner that esbuild emits in IIFE
  // mode when there's no name — there isn't one here, so nothing to do.
};

if (watch) {
  const ctx = await context(baseOptions);
  await ctx.watch();
  // eslint-disable-next-line no-console
  console.log('[build-frontend] watching for changes…');
} else {
  await build(baseOptions);
  // eslint-disable-next-line no-console
  console.log('[build-frontend] built bundles for:', PAGES.join(', '));
}
