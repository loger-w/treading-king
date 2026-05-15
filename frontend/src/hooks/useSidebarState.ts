import { useEffect, useState } from 'react';

const KEY = 'sidebar:expanded';

export function useSidebarState() {
  const [expanded, setExpanded] = useState<boolean>(() => {
    const raw = localStorage.getItem(KEY);
    return raw === null ? false : raw === '1';
  });

  useEffect(() => {
    localStorage.setItem(KEY, expanded ? '1' : '0');
  }, [expanded]);

  return [expanded, setExpanded] as const;
}
