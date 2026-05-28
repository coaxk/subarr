// Rules page mount.

import { TopBar, SubRail } from '../chrome.jsx';
import { RulesPage } from '../rules.jsx';

function App() {
  return (
    <div className="app-shell">
      <TopBar section="operations" />
      <div className="app-body">
        <SubRail section="operations" activeId="rules" />
        <RulesPage />
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
