// #546: probe roots that do not exist failed every scheduled walk silently, and
// the wizard offered `TV, Movies` whether or not those folders existed (#524).

export function missingRootsMessage(check) {
  if (!Array.isArray(check)) return '';
  const bad = check.filter((c) => c && c.ok === false);
  if (bad.length === 0) return '';
  const names = bad.map((c) => c.root).join(', ');
  const them = bad.length === 1 ? 'it' : 'them';
  return `Not found under the library root: ${names}. Scheduled walks skip ${them}, so nothing there is probed. Fix the name or remove ${them}, then Save.`;
}

export function initialRootsText(progressRoots, suggestions) {
  if (Array.isArray(progressRoots) && progressRoots.length) return progressRoots.join(', ');
  if (Array.isArray(suggestions) && suggestions.length) return suggestions.join(', ');
  return '';
}
