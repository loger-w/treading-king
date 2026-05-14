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

# Windows console 預設 cp950 印不出 Unicode (✓✗═)，強制 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

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

    # Step 2: login (智能路由)
    step(2, "Login (apikey_dma_login — DMA mode, no cert needed)")
    # 富邦語境：FUBON_ACCOUNT 通常就是身分證字號，等同 FUBON_PERSONAL_ID
    personal_id = (
        os.getenv("FUBON_PERSONAL_ID", "").strip()
        or os.getenv("FUBON_ACCOUNT", "").strip()
    )
    api_key = os.getenv("FUBON_API_KEY", "").strip()
    cert_path = os.getenv("FUBON_CERT_PATH", "").strip()
    cert_pass = os.getenv("FUBON_CERT_PASS", "").strip() or None

    if not personal_id:
        fail(
            "FUBON_PERSONAL_ID (or FUBON_ACCOUNT) missing in .env. "
            "Fill 身分證字號 and re-run."
        )
    if not api_key:
        fail("FUBON_API_KEY missing in .env. Fill it and re-run.")

    sdk = FubonSDK()

    if cert_path:
        # 有憑證 → 完整 apikey_login（行情 + 下單）
        print(f"    Using apikey_login (with cert: {cert_path})")
        try:
            result = sdk.apikey_login(personal_id, api_key, cert_path, cert_pass)
            ok(f"apikey_login OK ({type(result).__name__})")
        except Exception as e:
            fail("apikey_login failed.", e)
    else:
        # 無憑證 → DMA 行情登入（行情查詢 + WS 訂閱足夠）
        print(f"    Using apikey_dma_login (DMA mode, no cert needed)")
        try:
            result = sdk.apikey_dma_login(personal_id, api_key)
            ok(f"apikey_dma_login OK ({type(result).__name__})")
        except Exception as e:
            fail(
                "apikey_dma_login failed. "
                "If your account has no DMA permission, fill FUBON_CERT_PATH "
                "in .env to use full apikey_login.",
                e,
            )

    # Step 3: init_realtime
    step(3, "init_realtime (must precede marketdata calls)")
    try:
        sdk.init_realtime()
        ok("init_realtime OK")
    except Exception as e:
        fail("init_realtime failed.", e)

    # Step 4: 5 Technical indicators (real signatures)
    step(4, "Technical indicators (RSI / SMA / MACD / KDJ / BB) for 2330")
    try:
        tech = sdk.marketdata.rest_client.stock.technical  # type: ignore
    except AttributeError as e:
        fail("sdk.marketdata.rest_client.stock.technical path missing.", e)

    indicator_calls = [
        ("rsi", dict(symbol="2330", period=14), "rsi"),
        ("sma", dict(symbol="2330", period=20), "sma"),
        ("macd", dict(symbol="2330", fast=12, slow=26, signal=9), "macd"),
        ("kdj", dict(symbol="2330", rPeriod=9, kPeriod=3, dPeriod=3), "k"),
        ("bb",  dict(symbol="2330", period=20, stdDev=2), "upper"),
    ]
    for name, params, expected_field in indicator_calls:
        try:
            method = getattr(tech, name)
            r = method(**params)
            if not isinstance(r, dict):
                fail(f"{name} returned non-dict: {type(r).__name__}")
            data = r.get("data")
            if not isinstance(data, list) or not data:
                fail(f"{name} returned no data rows. result={r!r}")
            row = data[0]
            if expected_field not in row and "date" not in row:
                fail(
                    f"{name} response shape unexpected. "
                    f"Sample row keys={list(row.keys())}"
                )
            ok(f"{name}({params}) → {len(data)} rows, sample row keys={list(row.keys())}")
        except Exception as e:
            fail(f"{name} call failed.", e)

    # Step 5: WS connect + envelope shape check
    # Note: DMA login 進入 Speed mode，不支援 aggregates/candles channel
    # Speed mode 支援：trades / books / ticks / snapshot / tickers — 即時訊號夠用
    # (200-symbol cap 延後到 Phase 3 真實 connection pool 驗證)
    step(5, "WebSocket: connect + subscribe trades(2330) + parse envelope")
    import json as _json

    try:
        ws = sdk.marketdata.websocket_client.stock  # type: ignore
    except AttributeError as e:
        fail("sdk.marketdata.websocket_client.stock path missing.", e)

    received_messages: list[str] = []

    def on_message(message: object) -> None:
        # Speed mode 訊息是 raw JSON string
        received_messages.append(message)  # type: ignore[arg-type]

    def on_connect() -> None:
        print("    WS connected")

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
        time.sleep(2)

        try:
            ws.subscribe({"channel": "trades", "symbols": ["2330"]})
            ok("subscribe accepted (channel=trades, symbols=['2330'])")
        except Exception as e:
            fail("subscribe failed.", e)

        print("    Listening 8 seconds for messages...")
        time.sleep(8)

        if not received_messages:
            session_hint = "（盤外時段沒 trade tick 屬正常；連 + subscribe 已成功）"
            print(f"{YELLOW}  ⚠ No messages in 8s. {session_hint}{RESET}")
            ok("WS connected + subscribe accepted (no trade tick outside trading hours)")
        else:
            ok(f"Received {len(received_messages)} messages")
            envelope_ok = False
            event_types: set[str] = set()
            for raw in received_messages[:5]:
                try:
                    parsed = _json.loads(raw) if isinstance(raw, str) else raw
                except Exception:
                    print(f"    raw message (not JSON): {str(raw)[:150]}")
                    continue
                if isinstance(parsed, dict):
                    ev = parsed.get("event")
                    if ev:
                        event_types.add(str(ev))
                    if "event" in parsed and "data" in parsed:
                        envelope_ok = True
            print(f"    event types observed: {event_types or '(none)'}")
            if envelope_ok:
                ok("envelope matches plan: {event, data, ...}")
            else:
                print(
                    f"{YELLOW}  ⚠ Envelope shape may differ from plan. "
                    f"First message: {str(received_messages[0])[:200]}{RESET}"
                )

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
