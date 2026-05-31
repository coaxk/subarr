// Library page mount.

import { TopBar, SubRail } from '../chrome.jsx';
import { LibraryPage } from '../library.jsx';

function App() {
  return (
    <div className="app-shell">
      <TopBar section="library" />
      <div className="app-body">
        <SubRail section="library" activeId="browse" />
        <LibraryPage />
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
