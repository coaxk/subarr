// Timers scoped to a component's lifetime.
//
// Why this exists: `await new Promise(r => setTimeout(r, ms))` and a bare
// `setTimeout(fn, ms)` inside a component are DETACHED from React. Unmounting
// runs effect cleanups, but it cannot reach a timer awaited inside an async
// function, so the loop around it keeps sleeping, fetching and calling
// setState against a page that is gone. Review's re-probe poll ran for five
// minutes after the user left (#556), and Coverage's "Probe now" poll for
// three. The same shape also failed `frontend unit tests` on main twice, with
// every test passing, when a timer landed after vitest tore jsdom down.
//
// A scope owns every timer it starts. `dispose()` clears them all; after that
// `sleep()` never resolves, so each `await sleep(...)` is exactly where its
// loop stops, and `later()` callbacks never run.
//
// The factory is plain JS so it can be tested without a renderer;
// `useTimerScope()` is the thin hook the pages use.

export function createTimerScope() {
  const timers = new Set();
  let live = true;
  return {
    alive: () => live,
    // Resolves only while the scope is alive. After dispose the promise is
    // abandoned on purpose: the caller's loop stops at its next await.
    sleep(ms) {
      return new Promise((resolve) => {
        if (!live) return;
        const id = setTimeout(() => {
          timers.delete(id);
          resolve();
        }, ms);
        timers.add(id);
      });
    },
    // Fire-and-forget work that must not outlive the page.
    later(fn, ms) {
      if (!live) return null;
      const id = setTimeout(() => {
        timers.delete(id);
        fn();
      }, ms);
      timers.add(id);
      return id;
    },
    cancel(id) {
      if (id == null) return;
      timers.delete(id);
      clearTimeout(id);
    },
    dispose() {
      live = false;
      for (const id of timers) clearTimeout(id);
      timers.clear();
    },
  };
}

// React is a runtime global in these bundles (see vitest.setup.js and
// scripts/build-frontend.mjs), so the hook reads it from globalThis rather
// than importing a second copy.
export function useTimerScope() {
  const React = globalThis.React;
  const ref = React.useRef(null);
  if (!ref.current) ref.current = createTimerScope();
  React.useEffect(() => () => ref.current.dispose(), []);
  return ref.current;
}
