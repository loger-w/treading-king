import { useEffect, useRef } from "react";

/**
 * Esc 關閉 modal。callback 走 ref、listener 只註冊一次:
 * 直接依賴 inline onClose 的話,父層每次 re-render(行情 tick 頻率)
 * 都會把 window keydown listener remove/add 一輪。
 */
export function useEscapeKey(onEscape: () => void, enabled = true) {
  const ref = useRef(onEscape);
  ref.current = onEscape;
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") ref.current(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);
}
