import { useSidebarState } from '../hooks/useSidebarState';

export type Page = 'monitor' | 'mxf_backtest';

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
    id: 'mxf_backtest',
    label: '小台指策略回測 (MXF)',
    iconPath: 'M3 3v18h18 M7 14l4-4 3 3 5-7',
  },
];

interface Props {
  current: Page;
  onNavigate: (page: Page) => void;
}

export function Sidebar({ current, onNavigate }: Props) {
  const [expanded, setExpanded] = useSidebarState();

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
