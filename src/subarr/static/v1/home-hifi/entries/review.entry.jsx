// Review page mount — v1.1.1 dedicated audio-lang review queue.

import { TopBar, SubRail } from '../chrome.jsx';
import { ReviewPage } from '../review.jsx';

function App() {
  return (
    <div className="app-shell">
      <TopBar section="operations" />
      <div className="app-body">
        <SubRail section="operations" activeId="review" />
        <ReviewPage />
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
