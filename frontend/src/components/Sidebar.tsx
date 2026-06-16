import { useLocalToggle } from '../hooks/useLocalToggle';

export type Page = 'monitor' | 'auto_monitor' | 'mxf_backtest' | 'index_board';

interface NavItem {
  id: Page;
  label: string;
  iconPath: string;
}

const NAV_ITEMS: NavItem[] = [
  {
    id: 'monitor',
    label: '即時監控',
    iconPath: 'M3 12h3l3-8 4 16 3-10 2 6h3',
  },
  {
    id: 'auto_monitor',
    label: '自動監聽',
    iconPath: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2m-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zM17.9 17.39c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39',
  },
  {
    id: 'mxf_backtest',
    label: '小台指策略回測 (MXF)',
    iconPath: 'M3 3v18h18 M7 14l4-4 3 3 5-7',
  },
  {
    id: 'index_board',
    label: '大盤指數',
    iconPath: 'M3 17l5-6 4 4 5-8 4 5 M3 17v4h18',
  },
];

interface Props {
  current: Page;
  onNavigate: (page: Page) => void;
}

export function Sidebar({ current, onNavigate }: Props) {
  // 原 useSidebarState 與 useLocalToggle 重複實作 localStorage boolean 且少了
  // quota/private mode 保護;key 換 tk: 前綴慣例(舊值格式 1/0 與新格式不相容)
  const [expanded, setExpanded] = useLocalToggle('tk:sidebar:expanded', false);

  return (
    <aside
      className={[
        'absolute left-0 top-0 bottom-0 z-10 bg-bg-deep border-r border-line',
        'flex flex-col transition-[width] duration-200 ease-out',
        expanded
          ? 'w-[220px] shadow-[2px_0_12px_rgba(0,0,0,0.5)]'
          : 'w-[56px]',
      ].join(' ')}
    >
      <div
        className={
          expanded
            ? 'p-[18px] border-b border-line'
            : 'py-[18px] flex justify-center border-b border-line'
        }
      >
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-label="切換選單"
          aria-expanded={expanded}
          className="flex flex-col gap-[3px] w-4 text-ink-muted hover:text-ink transition-colors duration-150 ease-out cursor-pointer"
        >
          <span className="block h-[1.5px] bg-current" />
          <span className="block h-[1.5px] bg-current" />
          <span className="block h-[1.5px] bg-current" />
        </button>
      </div>

      <nav className="flex flex-col py-2">
        {NAV_ITEMS.map((item) => {
          const isActive = item.id === current;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onNavigate(item.id)}
              title={item.label}
              aria-current={isActive ? 'page' : undefined}
              className={[
                'relative flex items-center gap-3 transition-colors duration-150 ease-out cursor-pointer',
                expanded ? 'px-4 py-2.5' : 'px-0 py-3 justify-center',
                isActive
                  ? 'text-ink bg-accent/[0.05] before:absolute before:left-0 before:top-0 before:bottom-0 before:w-[2px] before:bg-accent'
                  : 'text-ink-muted hover:text-ink hover:bg-white/[0.02]',
              ].join(' ')}
            >
              <svg
                className={[
                  'w-4 h-4 shrink-0 transition-colors duration-150 ease-out',
                  isActive ? 'text-accent' : 'text-ink-dim',
                ].join(' ')}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d={item.iconPath} />
              </svg>
              <span className={expanded ? 'text-sm font-medium' : 'sr-only'}>
                {item.label}
              </span>
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
