// #479: two small decisions the wizard makes about connection tests.
//
// Configured-but-broken subgen URLs held at 15% of installs across two
// measurements, with "host resolves, port refuses" the leading cause. The
// cheapest fix is to test the URL the moment it is typed, not when (if) the
// user clicks Test, and to make skipping past a failed test a visible choice.

// Services that authenticate with an API key; the test needs both fields.
const KEYED = new Set(['bazarr', 'sonarr', 'radarr', 'tautulli']);

/** True when the step's fields are complete enough for a test to mean anything. */
export function readyToTest(service, progress) {
  const url = progress && progress[`${service}_url`];
  if (typeof url !== 'string') return false;
  // scheme, then at least one host character
  if (!/^https?:\/\/[^\s/]+/.test(url.trim())) return false;
  if (KEYED.has(service)) {
    const key = progress[`${service}_api_key`];
    if (typeof key !== 'string' || !key.trim()) return false;
  }
  return true;
}

/** What the primary button should say given the last test on this step. */
export function continueLabelFor({ isLast, testResult }) {
  const failed = !!(testResult && testResult.ok === false);
  if (isLast) return failed ? 'Finish anyway →' : 'Finish setup →';
  return failed ? 'Continue anyway →' : 'Continue →';
}
