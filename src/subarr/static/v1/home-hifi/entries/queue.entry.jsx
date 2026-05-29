// Queue page mount.

import { TopBar, SubRail } from '../chrome.jsx';
import { QueuePage } from '../queue.jsx';

function App() {
  return (
    <div className="app-shell">
      <TopBar section="operations" />
      <div className="app-body">
        <SubRail section="operations" activeId="queue" />
        <QueuePage />
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
