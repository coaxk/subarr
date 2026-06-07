// Health page mount (#157).

import { TopBar, SubRail } from '../chrome.jsx';
import { HealthPage } from '../health.jsx';

function App() {
  return (
    <div className="app-shell">
      <TopBar section="overview" />
      <div className="app-body">
        <SubRail section="overview" activeId="health" />
        <HealthPage />
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
