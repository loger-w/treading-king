"""群益 smoke:登入 → 憑證 → (可選)送測試單 / 查即時庫存。

用法(在 backend/,venv):
  python scripts/capital_smoke.py                # 只登入+憑證+狀態
  python scripts/capital_smoke.py --send-test    # 額外送一筆測試單(需 CAPITAL_ORDER_ENABLED=true)
  python scripts/capital_smoke.py --balance      # 查即時庫存,印原始字串(校準欄位假設表)

讀 backend/.env(CAPITAL_*)。--send-test 必須 CAPITAL_ENV=test;
--balance 唯讀(登入+查詢,不送單),prod 也允許 —— 測試沙盒群益端未開通,校準只能對 prod 做。
"""
from __future__ import annotations
import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from services.capital_factory import get_capital  # noqa: E402
from services.capital_models import StockOrderRequest, BuySell, PriceType  # noqa: E402


async def main(send_test: bool, balance: bool) -> int:
    env = os.getenv("CAPITAL_ENV", "test").strip()
    if env != "test" and (send_test or not balance):
        print("CAPITAL_ENV 不是 test,為安全中止(prod 只允許 --balance 唯讀查詢)。")
        return 2
    if env != "test":
        print("[prod] 帳號唯讀查詢:只登入+查庫存,不送單。")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    client = get_capital()
    if client is None:
        print("未設定 CAPITAL_USER_ID,無法測試。")
        return 2

    if balance:
        # 校準用:解析成功與否都印原始字串 —— 欄位 index 是假設表,
        # 「解析成功」也可能是錯位讀到別的欄,必須對原始字串校準
        orig_handle = client._handle_balance
        def tap(raw: str) -> None:
            print(f"RAW| {raw!r}")
            orig_handle(raw)
        client._handle_balance = tap

        orig_profit = client._handle_profit
        def tap_profit(raw: str) -> None:
            print(f"PNL| {raw!r}")
            orig_profit(raw)
        client._handle_profit = tap_profit

    client.start(asyncio.get_running_loop())
    # 等登入序列(輪詢 status,最多 ~20 秒)
    for _ in range(200):
        if client.status != "error":
            break
        await asyncio.sleep(0.1)
    print(f"狀態: {client.status}  最後錯誤: {client.last_error}")
    if client.status != "ok":
        return 1

    if send_test:
        req = StockOrderRequest(
            stock_no="2330", buy_sell=BuySell.BUY, price=500.0, qty=1,
            price_type=PriceType.LIMIT,
        )
        res = await client.submit_stock_order(req)
        print(f"送單結果: ok={res.ok} code={res.code} msg={res.message}")

    if balance:
        # 戳查詢排程,等 庫存 → 損益 兩段串行查詢的事件(啟動後第一圈本來就會查,這裡只是保險)
        client._mark_balance_dirty(delay_s=0.0)
        await asyncio.sleep(12)
        positions = client.store.positions()
        for p in positions:
            avg = f"{p.avg_price:.2f}" if p.avg_price is not None else "—"
            print(f"持倉: {p.stock_no} {p.qty} 張 ({p.kind}) 均 {avg}")
        if not positions:
            print("(清單空 — 若群益 App 有持倉,看 log 的 balance line 警告,校準 capital_balance.py 假設表)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--send-test", action="store_true")
    ap.add_argument("--balance", action="store_true", help="查即時庫存並印解析結果(首測校準欄位用)")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.send_test, args.balance)))
