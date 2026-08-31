// #252 — pure, DOM-free helpers for the Health page's Run-now control.
// Extracted so vitest can unit-test them without a React harness (mirrors
// log-helpers.mjs and instance-health-util.mjs).

// Turn a POST /api/health/tasks/<name>/run response into a message, or null
// when it worked.
//
// ⚠️ The reason this exists: `fetch` does NOT reject on an HTTP error status.
// The original handler was `.then(() => load()).catch(() => {})`, so a 409 ran
// the success path and the catch only ever saw network failures. Clicking Run
// now on a job that could not fire did nothing observable at all.
//
// That was survivable when the only triggers were an update poll and a feeder
// kick. It is not now: coverage-cache calls out to Bazarr, Sonarr and Plex, so
// "it did not fire" is an ordinary outcome the user has to be told about.
export function runFailureMessage(status, detail) {
  if (status >= 200 && status < 300) return null;
  // 409: declared runnable, but the trigger did not fire. Either its component
  // is not on app.state yet (early boot) or the trigger itself threw, which
  // run_job catches and reports as a non-fire.
  if (status === 409) return detail || 'could not run right now';
  // 400: the job does not support run-now. The button should not have rendered,
  // so this means the client and server disagree about the registry.
  if (status === 400) return detail || 'this job does not support run-now';
  if (status === 401 || status === 403) return 'not authorised';
  return detail || `failed (HTTP ${status})`;
}

// Pull FastAPI's `detail` out of a response body without assuming it parsed.
export function detailFrom(body) {
  if (!body || typeof body !== 'object') return '';
  const d = body.detail;
  return typeof d === 'string' ? d : '';
}
