// Settings page mount.

import { TopBar } from '../chrome.jsx';
import { SettingsPage } from '../settings.jsx';

function App() {
  return (
    <div className="app-shell">
      <TopBar section="config" healthKind="warn" healthLabel="4/5 healthy" />
      <SettingsPage />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
