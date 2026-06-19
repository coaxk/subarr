// React + ReactDOM are runtime CDN globals (the IIFE bundles leave them
// undefined-at-build, resolved on window). Our unit tests exercise pure
// logic, not rendering — but importing a .jsx module would ReferenceError if
// anything touches React/ReactDOM at module scope. Stub minimal globals so
// imports are safe without pulling React into devDependencies.

// Some JSX modules read window.location at module scope (e.g. arena.jsx reads
// URL search params to seed form state). Stub window so those imports don't
// ReferenceError in the Node test environment.
if (typeof globalThis.window === 'undefined') {
  globalThis.window = {
    location: { search: '', href: '', pathname: '/' },
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => {},
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    // api-fetch.mjs patches window.fetch at module scope; stub it so the
    // .bind() call doesn't throw in the Node test environment.
    fetch: () => Promise.reject(new Error('fetch not available in tests')),
    __subarrApiFetchInstalled: false,
  };
}
globalThis.React = globalThis.React || {
  createElement: () => null,
  Fragment: Symbol('React.Fragment'),
  useState: () => [undefined, () => {}],
  useEffect: () => {},
  useMemo: (fn) => fn(),
  useCallback: (fn) => fn,
  useRef: () => ({ current: null }),
  createContext: () => ({ Provider: () => null, Consumer: () => null }),
  // React.memo wraps a component — in test stubs just return the component unchanged.
  memo: (fn) => fn,
};
globalThis.ReactDOM = globalThis.ReactDOM || {
  createRoot: () => ({ render() {}, unmount() {} }),
};
