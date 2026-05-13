import { useEffect, useState } from "react";
import { api, type MeResponse } from "../lib/api";

let cached: MeResponse | null = null;

export function useMe(): MeResponse | null {
  const [me, setMe] = useState<MeResponse | null>(cached);

  useEffect(() => {
    if (cached) return;
    let alive = true;
    api.me().then((res) => {
      cached = res;
      if (alive) setMe(res);
    }).catch(() => {
      // /api/me 失敗代表 backend 起不來，畫面上的 SystemStatus 會處理錯誤訊息，
      // 這裡靜默即可。
    });
    return () => { alive = false; };
  }, []);

  return me;
}
