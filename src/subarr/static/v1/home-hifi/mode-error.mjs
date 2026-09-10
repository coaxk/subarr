// #524: /api/mode's error responses carry a `detail` string that already says
// what went wrong (permission denied and what to do, file missing, bad YAML).
// The card used to throw away the body and render "HTTP 503".
export function modeErrorMessage(status, body) {
  const detail = body && body.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  return `HTTP ${status}`;
}
