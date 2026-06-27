// #378: shared, React-free helpers for per-instance live health rendering.
// Used by the Settings ▸ Instances editor (row dots) and the Settings ▸
// Integrations summary tiles (per-instance sub-rows). Kept dependency-free so
// the dot-kind truth table lives in ONE place and unit-tests in a plain node
// env. Fed by GET /api/instances/health.

const INSTANCED_SERVICES = new Set(['sonarr', 'radarr', 'bazarr']);

// Index the /api/instances/health fan-out by `${service}/${id}` for O(1) lookup.
export function indexHealth(health) {
  const out = {};
  for (const h of health || []) out[`${h.service}/${h.id}`] = h;
  return out;
}

// Resolve a status-dot kind from a live health record.
//   online            → ok (green)    reachable right now
//   configured, down  → error (red)   has creds but the probe failed
//   not configured    → muted (grey)  nothing to reach
//   undefined (h)     → muted         probe still pending — unknown, not false-green
export function dotKindForInstance(_inst, health) {
  if (!health || !health.configured) return 'muted';
  return health.online ? 'ok' : 'error';
}

// Per-instance sub-rows for an Integrations summary tile. Returns [] for
// non-instanced services (plex/tautulli/subgen/ollama) and for instanced
// services that have only the default instance — single-stack installs stay
// visually identical. Otherwise one row per instance, default first.
export function instanceSubRows(serviceId, instancesHealth) {
  const svc = (serviceId || '').toLowerCase();
  if (!INSTANCED_SERVICES.has(svc)) return [];
  const rows = (instancesHealth || []).filter((h) => h.service === svc);
  if (rows.length <= 1) return [];
  return rows
    .slice()
    .sort((a, b) => Number(b.id === '') - Number(a.id === '') || (a.name || '').localeCompare(b.name || ''))
    .map((h) => ({
      id: h.id,
      name: h.id === '' ? 'default' : h.name || h.id,
      kind: dotKindForInstance(null, h),
      label: !h.configured
        ? 'not configured'
        : h.online
          ? 'connected'
          : `offline${h.detail ? ` — ${h.detail}` : ''}`,
    }));
}
