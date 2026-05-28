/**
 * subarr.com apex redirect.
 *
 * v1.0 landing strategy: redirect the bare apex domain to the GitHub
 * repo's README. Costs nothing to maintain, sends visitors straight to
 * the source of truth, and never gets out of sync with the project.
 *
 * Routes (bind in Cloudflare dashboard):
 *   - subarr.com/*    → 301 redirect to GitHub
 *   - www.subarr.com/* → same
 *
 * Bots and robots.txt: returns a minimal robots.txt so crawlers don't
 * index the redirect chain. /favicon.ico responds 204 to avoid the
 * 404 noise in Cloudflare analytics.
 *
 * Excluded routes (handled by other workers):
 *   - telemetry.subarr.com/*  → telemetry receiver (separate worker)
 *
 * Replace REDIRECT_TARGET when we ship a real landing site.
 */

const REDIRECT_TARGET = 'https://github.com/coaxk/subarr';

const ROBOTS_TXT = [
  'User-agent: *',
  'Disallow: /',
].join('\n');

export default {
  async fetch(request) {
    const url = new URL(request.url);

    if (url.pathname === '/robots.txt') {
      return new Response(ROBOTS_TXT, {
        status: 200,
        headers: { 'content-type': 'text/plain; charset=utf-8' },
      });
    }

    if (url.pathname === '/favicon.ico') {
      return new Response(null, { status: 204 });
    }

    // Preserve the request path on the redirect — visitors who land at
    // subarr.com/install or similar get a sensible fallback to the
    // README anchor section rather than a 404 from GitHub.
    const target = new URL(REDIRECT_TARGET);
    if (url.pathname && url.pathname !== '/') {
      target.hash = `readme-${url.pathname.replace(/^\//, '').replace(/\//g, '-')}`;
    }

    return Response.redirect(target.toString(), 301);
  },
};
