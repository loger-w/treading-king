import { useEffect, useState } from 'react';
import { Sidebar, type Page } from './components/Sidebar';
import { AutoMonitor } from './pages/AutoMonitor';
import { Monitor } from './pages/Monitor';
import { MXFBacktest } from './pages/MXFBacktest';
import { IndexBoard } from './pages/IndexBoard';

export default function App() {
  const [page, setPage] = useState<Page>('monitor');

  useEffect(() => {
    document.body.classList.add('opacity-100');
  }, []);

  return (
    <div className="h-screen relative overflow-hidden">
      <Sidebar current={page} onNavigate={setPage} />
      <div className="ml-[56px] h-screen overflow-hidden relative">
        {/* 三頁恆常 mount(keep-alive:切頁保留狀態、WS 常駐),
            active 讓隱藏頁暫停打富邦的 REST 輪詢——背景圖表白耗 rate limit */}
        <div hidden={page !== 'monitor'} className="h-full">
          <Monitor active={page === 'monitor'} />
        </div>
        <div hidden={page !== 'auto_monitor'} className="h-full">
          <AutoMonitor active={page === 'auto_monitor'} />
        </div>
        <div hidden={page !== 'mxf_backtest'} className="h-full">
          <MXFBacktest active={page === 'mxf_backtest'} />
        </div>
        <div hidden={page !== 'index_board'} className="h-full">
          <IndexBoard active={page === 'index_board'} />
        </div>
      </div>
    </div>
  );
}
