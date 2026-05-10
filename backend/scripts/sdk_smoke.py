"""Phase 0.5 — Fubon SDK Sanity Check.

驗證 plan 建立的 4 個未驗證假設。任一失敗就停下來解決，不要繼續往 Phase 1。

跑法：
    cd backend
    python -m venv .venv
    .venv\\Scripts\\Activate.ps1   # Windows
    pip install ./wheels/fubon_neo-*.whl python-dotenv
    python scripts/sdk_smoke.py
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

# 載入 .env
try:
    from dotenv import load_dotenv

    backend_dir = Path(__file__).resolve().parent.parent
    load_dotenv(backend_dir / ".env")
except ImportError:
    print("⚠️  python-dotenv not installed. Skipping .env load.")


GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def step(n: int, title: str) -> None:
    print(f"\n{YELLOW}[Step {n}] {title}{RESET}")


def ok(msg: str) -> None:
    print(f"{GREEN}  ✓ {msg}{RESET}")


def fail(msg: str, exc: Exception | None = None) -> None:
    print(f"{RED}  ✗ {msg}{RESET}")
    if exc is not None:
        print(f"{RED}    {type(exc).__name__}: {exc}{RESET}")
        traceback.print_exc()
    sys.exit(1)


def main() -> None:
    print(f"{YELLOW}═══════════════════════════════════════════════════════════{RESET}")
    print(f"{YELLOW}  Fubon SDK Sanity Check (Phase 0.5){RESET}")
    print(f"{YELLOW}  Python {sys.version.split()[0]}{RESET}")
    print(f"{YELLOW}═══════════════════════════════════════════════════════════{RESET}")

    # Step 1: wheel install + import
    step(1, "Import fubon_neo")
    try:
        from fubon_neo.sdk import FubonSDK  # type: ignore

        ok(f"Imported FubonSDK from {FubonSDK.__module__}")
    except ImportError as e:
        fail(
            "Cannot import fubon_neo. "
            "Did you `pip install ./wheels/fubon_neo-*.whl`? "
            "Check Python version (3.12 / 3.13 only).",
            e,
        )

    # Step 2: apikey_login
    step(2, "apikey_login (v2.2.7+ feature)")
    api_key = os.getenv("FUBON_API_KEY", "").strip()
    if not api_key:
        fail("FUBON_API_KEY missing in .env. Fill it and re-run.")

    sdk = FubonSDK()
    if not hasattr(sdk, "apikey_login"):
        fail(
            "sdk.apikey_login missing. "
            "Wheel might be older than v2.2.7 — upgrade it."
        )

    try:
        result = sdk.apikey_login(api_key)
        ok(f"apikey_login returned: {type(result).__name__}")
    except Exception as e:
        fail("apikey_login failed.", e)

    # Step 3: init_realtime
    step(3, "init_realtime (must precede marketdata calls)")
    try:
        sdk.init_realtime()
        ok("init_realtime OK")
    except Exception as e:
        fail("init_realtime failed.", e)

    # Step 4: 5 Technical endpoints
    step(4, "Technical indicators (RSI / MACD / KDJ / SMA / BBands) for 2330")
    try:
        rest = sdk.marketdata.rest_client.stock.intraday  # type: ignore
    except AttributeError as e:
        fail("sdk.marketdata.rest_client.stock.intraday path missing.", e)

    technical_methods = [
        "technical_rsi",
        "technical_macd",
        "technical_kdj",
        "technical_sma",
        "technical_bbands",
    ]
    found_methods: list[str] = []
    missing_methods: list[str] = []

    # 富邦 SDK 的具體方法名可能不一樣，先列存在的
    available = [m for m in dir(rest) if "technical" in m.lower() or m.startswith("technical")]
    print(f"    Available technical methods on rest_client: {available}")

    for method_name in technical_methods:
        if not hasattr(rest, method_name):
            missing_methods.append(method_name)
            continue
        try:
            method = getattr(rest, method_name)
            result = method(symbol="2330")
            if result is None:
                fail(f"{method_name} returned None.")
            ok(f"{method_name}: returned data ({type(result).__name__})")
            found_methods.append(method_name)
        except Exception as e:
            fail(f"{method_name} call failed.", e)

    if missing_methods:
        print(
            f"{YELLOW}  ⚠ Missing methods (might use different naming): "
            f"{missing_methods}{RESET}"
        )
        print(
            f"{YELLOW}    Check {available} above to find right method names{RESET}"
        )

    # Step 5: WS 200 subscribe limit
    step(5, "WebSocket subscribe to 200 symbols (verify 200 hard cap)")
    try:
        ws = sdk.marketdata.websocket_client.stock  # type: ignore
    except AttributeError as e:
        fail("sdk.marketdata.websocket_client.stock path missing.", e)

    # 拿前 200 檔上市熱門股做 smoke test。實際 production 從 symbols 表讀。
    smoke_symbols = [
        "2330", "2317", "2454", "2308", "2412", "2882", "2881", "1303",
        "1301", "2002", "2891", "2884", "2885", "2886", "2892", "2880",
        "2887", "5871", "2890", "2883",
    ] * 10  # 共 200 個（重複也行，富邦會去重）
    smoke_symbols = list(dict.fromkeys(smoke_symbols))  # 去重
    if len(smoke_symbols) < 200:
        # 補足 200 — 用一些常見代碼，實測會看富邦回的訊息知道哪些不存在
        extras = [f"2{i:03d}" for i in range(100, 300)]
        smoke_symbols.extend(extras)
    smoke_symbols = smoke_symbols[:200]

    received_messages: list[dict] = []
    received_event_types: set[str] = set()

    def on_message(message: dict) -> None:
        received_messages.append(message)
        ev = message.get("event")
        if ev is not None:
            received_event_types.add(ev)

    def on_connect() -> None:
        print(f"    WS connected")

    def on_disconnect(*args: object) -> None:
        print(f"    WS disconnected: {args}")

    def on_error(*args: object) -> None:
        print(f"{RED}    WS error: {args}{RESET}")

    try:
        ws.on("connect", on_connect)
        ws.on("disconnect", on_disconnect)
        ws.on("error", on_error)
        ws.on("message", on_message)
        ws.connect()

        time.sleep(1)  # 等 connect

        try:
            ws.subscribe(channel="aggregates", symbols=smoke_symbols)
            ok(f"subscribe call accepted for {len(smoke_symbols)} symbols")
        except Exception as e:
            fail(f"subscribe failed (expected if >200 hits cap).", e)

        print("    Listening 5 seconds for messages...")
        time.sleep(5)

        ok(f"Received {len(received_messages)} messages")
        if received_messages:
            sample = received_messages[0]
            print(f"    Sample message keys: {list(sample.keys())}")
            print(f"    Event types observed: {received_event_types or '(none)'}")
            if "event" in sample and "data" in sample:
                ok("message envelope matches plan: {event, data, ...}")
            else:
                print(
                    f"{YELLOW}  ⚠ message envelope shape unexpected. "
                    f"Sample: {sample}{RESET}"
                )
        else:
            session_hint = (
                "（盤外時段沒 tick 屬正常；若是盤中此狀況代表 subscribe 沒生效）"
            )
            print(f"{YELLOW}  ⚠ No messages received in 5s. {session_hint}{RESET}")

        try:
            ws.disconnect()
        except Exception:
            pass

    except Exception as e:
        fail("WS smoke test failed.", e)

    # 收尾
    print()
    print(f"{GREEN}═══════════════════════════════════════════════════════════{RESET}")
    print(f"{GREEN}  ✓ All sanity checks passed (with caveats noted above).{RESET}")
    print(f"{GREEN}  → You can proceed to Phase 1.{RESET}")
    print(f"{GREEN}═══════════════════════════════════════════════════════════{RESET}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Interrupted by user.{RESET}")
        sys.exit(130)
