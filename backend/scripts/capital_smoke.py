"""群益測試環境 smoke:登入 → 憑證 → (可選)送一筆測試單。

用法(在 backend/,venv):
  python scripts/capital_smoke.py                # 只登入+憑證+狀態
  python scripts/capital_smoke.py --send-test    # 額外送一筆測試單(需 CAPITAL_ORDER_ENABLED=true)

讀 backend/.env(CAPITAL_*)。CAPITAL_ENV 必須 = test。
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from services.capital_factory import get_capital  # noqa: E402
from services.capital_models import StockOrderRequest, BuySell, PriceType  # noqa: E402


async def main(send_test: bool, balance: bool) -> int:
    if os.getenv("CAPITAL_ENV", "test").strip() != "test":
        print("CAPITAL_ENV 不是 test,為安全中止。")
        return 2
    client = get_capital()
    if client is None:
        print("未設定 CAPITAL_USER_ID,無法測試。")
        return 2

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
        # 戳查詢排程,等 OnRealBalanceReport 事件;原始字串看 log(capital_balance 解析警告)
        client._mark_balance_dirty(delay_s=0.0)
        await asyncio.sleep(5)
        positions = client.store.positions()
        for p in positions:
            print(f"持倉: {p.stock_no} {p.qty} 張 均 {p.avg_price}")
        if not positions:
            print("(清單空 — 若群益 App 有持倉,看 log 的 balance line 警告,校準 capital_balance.py 假設表)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--send-test", action="store_true")
    ap.add_argument("--balance", action="store_true", help="查即時庫存並印解析結果(首測校準欄位用)")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.send_test, args.balance)))
