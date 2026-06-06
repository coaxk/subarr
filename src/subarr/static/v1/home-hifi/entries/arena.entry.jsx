// Tuning Lab page mount — #131 arena config-sweep + judge.

import { TopBar, SubRail } from '../chrome.jsx';
import { ArenaPage } from '../arena.jsx';

function App() {
  return (
    <div className="app-shell">
      <TopBar section="operations" />
      <div className="app-body">
        <SubRail section="operations" activeId="arena" />
        <ArenaPage />
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
