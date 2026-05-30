// Logs page mount.

import { TopBar, SubRail } from '../chrome.jsx';
import { LogsPage } from '../logs.jsx';

function App() {
  return (
    <div className="app-shell">
      <TopBar section="operations" />
      <div className="app-body">
        <SubRail section="operations" activeId="logs" />
        <LogsPage />
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
