"""下單稽核 —— 每筆寫入(含被擋下的)append 到 jsonl。"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from services.capital_models import StockOrderRequest, OrderResult

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "capital_orders.jsonl"


def write(
    path: Path,
    *,
    env: str,
    req: StockOrderRequest,
    blocked: str | None = None,
    result: OrderResult | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "env": env,
        "req": req.model_dump(mode="json"),
        "blocked": blocked,
        "result": result.model_dump(mode="json") if result else None,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
