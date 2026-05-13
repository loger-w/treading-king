import { useEffect, useState } from "react";

/**
 * 包 localStorage 的 boolean useState。
 *
 * - 首次讀 localStorage[key]，沒有就用 defaultValue
 * - setValue 自動同步寫回（quota / private mode 失敗時靜默吞掉）
 *
 * Naming convention: key 用 "tk:" 前綴（trading-king）→ "tk:chart:cdp" 之類。
 */
export function useLocalToggle(
  key: string,
  defaultValue: boolean,
): [boolean, (v: boolean | ((prev: boolean) => boolean)) => void] {
  const [value, setValue] = useState<boolean>(() => {
    if (typeof window === "undefined") return defaultValue;
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? defaultValue : raw === "true";
    } catch {
      return defaultValue;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(key, String(value));
    } catch {
      /* quota exceeded / private mode — 靜默 */
    }
  }, [key, value]);

  return [value, setValue];
}
