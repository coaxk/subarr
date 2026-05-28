// Onboarding wizard mount.
//
// React + ReactDOM come from CDN <script> tags loaded before this bundle,
// so they're available as runtime globals — no import. esbuild's IIFE
// format leaves the identifiers unresolved at build time and they bind
// at runtime to window.React / window.ReactDOM.

import { OnboardingPage } from '../onboarding.jsx';

ReactDOM.createRoot(document.getElementById('root')).render(
  <OnboardingPage />,
);
