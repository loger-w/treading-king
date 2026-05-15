import { useEffect, useState } from 'react';
import { Sidebar, type Page } from './components/Sidebar';
import { Monitor } from './pages/Monitor';
import { MicroFutBacktest } from './pages/MicroFutBacktest';

export default function App() {
  const [page, setPage] = useState<Page>('monitor');

  useEffect(() => {
    document.body.classList.add('opacity-100');
  }, []);

  return (
    <div className="h-screen relative overflow-hidden">
      <Sidebar current={page} onNavigate={setPage} />
      <div className="ml-[56px] h-screen overflow-hidden relative">
        <div hidden={page !== 'monitor'} className="h-full">
          <Monitor />
        </div>
        <div hidden={page !== 'backtest'} className="h-full">
          <MicroFutBacktest />
        </div>
      </div>
    </div>
  );
}
