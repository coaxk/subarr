// #157 gap-fill — pure, DOM-free helpers shared by logs.jsx (source switcher)
// and health.jsx (recent-errors panel). Extracted so vitest can unit-test them
// without a React/DOM harness (mirrors instance-health-util.mjs).

// The two log sources subarr can live-stream. subgen = the container log
// (/api/logs/events); subarr = subarr's own in-process ring
// (/api/logs/subarr/events).
export const LOG_SOURCES = ['subgen', 'subarr'];

// Toggle between the two sources; unknown input falls back to 'subgen'.
export function nextLogSource(current) {
  return current === 'subgen' ? 'subarr' : 'subgen';
}

// Shape a /api/logs/recent ring record into a display row for the Health
// "Recent errors" panel. Defensive against partial records.
export function formatRecentRow(rec) {
  const r = rec || {};
  const exc = r.exc_text || null;
  return {
    ts: typeof r.ts === 'number' ? r.ts : null,
    level: r.level || 'INFO',
    logger: r.logger_name || '',
    message: r.message || '',
    exc_text: exc,
    hasTrace: !!exc,
  };
}
