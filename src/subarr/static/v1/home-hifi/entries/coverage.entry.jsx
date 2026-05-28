// Coverage page mount.

import { TopBar, SubRail } from '../chrome.jsx';
import { CoveragePage } from '../coverage.jsx';

function App() {
  return (
    <div className="app-shell">
      <TopBar section="operations" />
      <div className="app-body">
        <SubRail section="operations" activeId="coverage" />
        <CoveragePage />
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
