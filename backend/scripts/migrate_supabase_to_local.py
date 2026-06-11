"""一次性:把共用 Supabase 裡某 user_label 的個人資料 + 訊號歷史拉到本機。

用法:
    cd backend
    python -m scripts.migrate_supabase_to_local --user-label loger

讀 .env 的 SUPABASE_URL / SUPABASE_KEY;--user-label 只用來決定拉哪個人的舊資料,
不寫入本機、不成為執行期概念。symbols / daily_ohlc 不遷(本機自行重建)。
"""
from __future__ import annotations

import argparse
import os
from typing import Any

from services.local_store import get_local_store

# PostgREST 預設單次回應上限 1000 筆,不分頁會靜默截斷
# (實際發生過:signals_log 漏了 435 筆)
_PAGE_SIZE = 1000


def _strip_label(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "user_label"}


def migrate(sb: Any, user_label: str, *, force: bool = False) -> dict:
    from services.local_store.paths import signals_log_path
    if signals_log_path().exists() and not force:
        raise RuntimeError(
            f"{signals_log_path()} 已存在 — 遷移疑似已執行過。"
            f"為避免訊號歷史重複(append-only 無法復原),已中止。"
            f"確定要重跑請先刪除該檔,或用 --force。"
        )
    store = get_local_store()
    store.init()
    cfg = store.config

    def pull(table: str, *, filter_label: bool = True) -> list[dict]:
        # .order("id") 讓分頁切點穩定、JSONL 順序可重現;
        # 迴圈撈到「回傳筆數 < 頁大小」為止,避免 1000 筆截斷
        out: list[dict] = []
        while True:
            q = sb.table(table).select("*")
            if filter_label:
                q = q.eq("user_label", user_label)
            res = q.order("id").range(len(out), len(out) + _PAGE_SIZE - 1).execute()
            page = res.data or []
            out.extend(page)
            if len(page) < _PAGE_SIZE:
                return out

    groups = [_strip_label(g) for g in pull("bookmark_groups") if not g.get("is_system")]
    # watchlist_items 沿用「拉全體、本機用自己的 group_id 過濾」—
    # 分頁吃在全體資料上,截斷風險最高,更需要撈完
    items = [_strip_label(i) for i in pull("watchlist_items", filter_label=False)
             if any(i["group_id"] == g["id"] for g in groups)]
    signals = [_strip_label(s) for s in pull("active_signals")]
    monitor = [_strip_label(m) for m in pull("monitor_list")]

    cfg.import_config({
        "schema_version": 1,
        "bookmark_groups": groups,
        "watchlist_items": items,
        "active_signals": signals,
        "monitor_list": monitor,
    })

    log_rows = [_strip_label(r) for r in pull("signals_log")]
    for r in log_rows:
        store.signals.append(r)

    summary = {"bookmark_groups": len(groups), "watchlist_items": len(items),
               "active_signals": len(signals), "monitor_list": len(monitor),
               "signals_log": len(log_rows)}
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-label", required=True)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv()
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    summary = migrate(sb, args.user_label, force=args.force)
    print("遷移完成:", summary)
    print("驗證無誤後,即可從 .env 移除 SUPABASE_*、解除安裝 supabase 依賴(見 Task 19)。")


if __name__ == "__main__":
    main()
