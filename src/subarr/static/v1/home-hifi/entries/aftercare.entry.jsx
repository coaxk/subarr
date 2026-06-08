// Aftercare page mount (#156).

import { TopBar, SubRail } from '../chrome.jsx';
import { AftercarePage } from '../aftercare.jsx';

function App() {
  return (
    <div className="app-shell">
      <TopBar section="operations" />
      <div className="app-body">
        <SubRail section="operations" activeId="aftercare" />
        <AftercarePage />
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
