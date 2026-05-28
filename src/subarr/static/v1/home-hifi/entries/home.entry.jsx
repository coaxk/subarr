// Home dashboard mount.

import { TopBar, SubRail } from '../chrome.jsx';
import {
  PageHeader,
  StagesRow,
  HostStrip,
  NextRunActivitySplit,
  useLiveDashboard,
} from '../dashboard.jsx';
import { fmtTime } from '../atoms.jsx';

function App() {
  // Live clock (1s tick) — purely client-side.
  const [now, setNow] = React.useState(fmtTime(new Date()));
  React.useEffect(() => {
    const id = setInterval(() => setNow(fmtTime(new Date())), 1000);
    return () => clearInterval(id);
  }, []);

  // Live dashboard data — /api/home/dashboard polled every 5s.
  // Each section falls back to demo constants when its slice is null
  // (first paint, or backend unreachable).
  const live = useLiveDashboard();

  return (
    <div className="app-shell">
      <TopBar section="overview" />
      <div className="app-body">
        <SubRail section="overview" activeId="dashboard" />
        <main className="main-canvas">
          <PageHeader now={now} />
          <StagesRow data={live && live.stages} />
          <HostStrip
            integrations={live && live.integrations}
            gpu={live && live.gpu}
          />
          <NextRunActivitySplit
            nextRun={live && live.next_run}
            activity={live && live.activity}
          />
        </main>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
