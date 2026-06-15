// Home dashboard mount.

import { TopBar, SubRail } from '../chrome.jsx';
import {
  PageHeader,
  StagesRow,
  HostStrip,
  NextRunActivitySplit,
  WelcomeCard,
  UpdateNudgeCard,
  NoAuthWarningCard,
  AfterCarePanel,
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

  // Live dashboard data — /api/home/dashboard polled every 5s. Sections
  // render honest empty/loading states when their slice is null (#193 —
  // never demo data).
  const live = useLiveDashboard();

  return (
    <div className="app-shell">
      <TopBar section="overview" />
      <div className="app-body">
        <SubRail section="overview" activeId="dashboard" />
        <main className="main-canvas">
          <PageHeader now={now} />
          <NoAuthWarningCard />
          <WelcomeCard />
          <UpdateNudgeCard />
          <StagesRow data={live && live.stages} />
          <AfterCarePanel />
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
