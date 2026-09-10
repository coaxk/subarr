// #479: a subgen outage the dashboard should refuse to let pass quietly.
//
// Eight installs were walking real libraries against a dead subgen, one for
// 66 days. The integration tile's red dot evidently did not stop that, and
// for most of any outage it was not even red (the watchdog kept the last
// good capabilities). This is the persistent, dismissable version.

// Show only once the outage has lasted this long: a container restart or a
// network blip must not produce a banner.
export const OUTAGE_MIN_SECONDS = 600;

export function formatDuration(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const units = [
    ['day', 86400], ['hour', 3600], ['minute', 60], ['second', 1],
  ];
  const parts = [];
  let rest = s;
  for (const [name, size] of units) {
    const n = Math.floor(rest / size);
    if (n > 0 || (parts.length === 0 && size === 1)) {
      parts.push(`${n} ${name}${n === 1 ? '' : 's'}`);
      rest -= n * size;
    }
    if (parts.length === 2) break;
  }
  return parts.join(' ');
}

/** Show when the outage is long enough and this exact outage was not dismissed. */
export function shouldShowOutage(outage, dismissedSince) {
  if (!outage || typeof outage.seconds !== 'number') return false;
  if (outage.seconds < OUTAGE_MIN_SECONDS) return false;
  if (dismissedSince != null && dismissedSince === outage.since) return false;
  return true;
}

export function outageMessage(outage) {
  return {
    title: `subgen has been unreachable for ${formatDuration(outage.seconds)}`,
    body: `Nothing is being transcribed while it is down. Every probe since then failed with "${outage.cause}".`,
    hint: outage.hint || '',
  };
}
