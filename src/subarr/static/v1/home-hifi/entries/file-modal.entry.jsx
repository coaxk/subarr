// Per-file verdict modal mount.

import { TopBar, SubRail } from '../chrome.jsx';
import { FileModalPage } from '../file-modal.jsx';

function App() {
  return (
    <div className="app-shell">
      <TopBar section="operations" />
      <div className="app-body">
        <SubRail section="operations" activeId="activity" />
        <FileModalPage />
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
