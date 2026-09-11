// #536: what the Logs page shows for a `stream_error` event. The backend sends
// `{detail, reason, container}`; older backends sent a plain string, which is
// treated as the socket case (the only one that existed then).

export function streamErrorPanel(err) {
  const obj = (err && typeof err === 'object') ? err : { reason: 'socket', detail: err };
  const detail = obj.detail == null ? '' : String(obj.detail);
  if (obj.reason === 'container_not_found') {
    const name = obj.container || 'subgen';
    return {
      title: `No container named "${name}"`,
      body:
        `Docker is reachable, but it has no container called "${name}". ` +
        'subarr reads that name from SUBGEN_CONTAINER, and it must match the ' +
        "container's name exactly (check for a typo, and that the container is " +
        'created, not just defined in a compose file). Fix the variable and ' +
        'recreate subarr, or unset it and let onboarding auto-detect the right one.',
      detail,
    };
  }
  return {
    title: "Can't reach Docker",
    body:
      "The Logs viewer streams subgen's container log, which needs Docker " +
      "socket access \u2014 and subarr's container can't reach it, so there's " +
      'nothing to show here. Give the subarr container the socket: bind-mount ' +
      '/var/run/docker.sock (read-only is fine) into subarr, or point it at a ' +
      'socket-proxy, then reload. Everything else in subarr works without it \u2014 ' +
      'only this Logs viewer needs it.',
    detail,
  };
}
