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


def _strip_label(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "user_label"}


def migrate(sb: Any, user_label: str) -> dict:
    store = get_local_store()
    store.init()
    cfg = store.config

    def pull(table: str) -> list[dict]:
        res = sb.table(table).select("*").eq("user_label", user_label).execute()
        return res.data or []

    groups = [_strip_label(g) for g in pull("bookmark_groups") if not g.get("is_system")]
    items = [_strip_label(i) for i in
             (sb.table("watchlist_items").select("*").execute().data or [])
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
    args = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv()
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    summary = migrate(sb, args.user_label)
    print("遷移完成:", summary)
    print("驗證無誤後,即可從 .env 移除 SUPABASE_*、解除安裝 supabase 依賴(見 Task 19)。")


if __name__ == "__main__":
    main()
