// #516: POST /api/audio-lang/verifications persists the verification locally
// and then tries Sonarr propagation best-effort. It answers 200 either way,
// with the outcome in `sonarr_propagation`:
//   { attempted: false }                       propagation is off / not applicable
//   { attempted: true, ok: true, ... }         Sonarr now records the language
//   { attempted: true, ok: false, reason, detail }
// Every caller used to check only `r.ok`, so the third shape was invisible:
// rows left Review looking fully successful while Sonarr still said English
// and Bazarr stayed blind. This is the ONE place that reads the block.

/** The failure, or null when there is nothing to tell the user. */
export function propagationFailure(body) {
  const p = body && body.sonarr_propagation;
  if (!p || !p.attempted || p.ok !== false) return null;
  return {
    reason: p.reason || 'unknown',
    detail: p.detail || 'Sonarr propagation failed',
  };
}

/**
 * Collapse per-file failures into one row per reason, most frequent first,
 * carrying one sample detail. A stale coverage snapshot fails every file of a
 * batch with the same message; the user needs that as one line, not 400.
 */
export function groupPropagationFailures(failures) {
  const byReason = new Map();
  for (const f of failures || []) {
    const g = byReason.get(f.reason);
    if (g) g.count += 1;
    else byReason.set(f.reason, { reason: f.reason, count: 1, detail: f.detail });
  }
  return Array.from(byReason.values()).sort((a, b) => b.count - a.count);
}
