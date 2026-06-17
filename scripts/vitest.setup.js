// React + ReactDOM are runtime CDN globals (the IIFE bundles leave them
// undefined-at-build, resolved on window). Our unit tests exercise pure
// logic, not rendering — but importing a .jsx module would ReferenceError if
// anything touches React/ReactDOM at module scope. Stub minimal globals so
// imports are safe without pulling React into devDependencies.
globalThis.React = globalThis.React || {
  createElement: () => null,
  Fragment: Symbol('React.Fragment'),
  useState: () => [undefined, () => {}],
  useEffect: () => {},
  useMemo: (fn) => fn(),
  useCallback: (fn) => fn,
  useRef: () => ({ current: null }),
  createContext: () => ({ Provider: () => null, Consumer: () => null }),
};
globalThis.ReactDOM = globalThis.ReactDOM || {
  createRoot: () => ({ render() {}, unmount() {} }),
};
