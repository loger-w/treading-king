"""群益登入探針(診斷用):逐步印出 SetAuthority / Login / 憑證 的每個回傳碼。

跟 capital_smoke 的差別:這支只認證、**完全沒有下單路徑**,所以可以安全地測正式環境
(用來分辨「連不上是測試端點專屬,還是網路/帳密問題」)。

用法(在 backend/,venv):
  python scripts/capital_login_probe.py test   # Authority 2:測試(模擬沙盒)
  python scripts/capital_login_probe.py prod   # Authority 0:正式 —— 會登入你的真實帳號(僅認證+讀憑證)

讀 backend/.env 的 CAPITAL_USER_ID / CAPITAL_PASSWORD / CAPITAL_DLL_DIR。
"""
from __future__ import annotations
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from services.capital_com import SkcomCapitalCom  # noqa: E402


def main(env: str) -> int:
    user_id = (os.getenv("CAPITAL_USER_ID") or "").strip()
    password = (os.getenv("CAPITAL_PASSWORD") or "").strip()
    dll_dir = os.getenv("CAPITAL_DLL_DIR") or None
    if not user_id or not password:
        print("缺 CAPITAL_USER_ID / CAPITAL_PASSWORD,無法測試。")
        return 2

    flag = 2 if env == "test" else 0
    label = "2:測試" if flag == 2 else "0:正式"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] 登入探針  env={env}  Authority={label}  user={user_id[:4]}***")
    if flag == 0:
        print("⚠️  正式環境:這會登入你的真實帳號(僅認證+讀憑證,本探針不送單)。")

    import pythoncom
    pythoncom.CoInitialize()
    com = SkcomCapitalCom(dll_dir=dll_dir)
    try:
        com.setup()
        print("  setup()        SKCOM 載入 + reply 事件註冊 OK")
        # SetAuthority 回傳值用原始碼印(它的語意與 Login 回傳碼不同,不硬套訊息表)
        auth = com.set_authority(flag)
        print(f"  SetAuthority   flag={flag} -> {auth}")
        code = com.login(user_id, password)
        print(f"  Login          -> {code}  ({com.return_code_message(code)})")
        if code != 0:
            print("  → 登入未通過,停在此步(下面的憑證/初始化不做)。")
            return 1
        print(f"  InitOrder      -> {com.init_order()}")
        cert = com.read_cert(user_id)
        print(f"  ReadCertByID   -> {cert}  ({com.return_code_message(cert)})")
        ok = cert == 0
        print("  狀態: ok" if ok else "  狀態: 登入成功但憑證未過")
        return 0 if ok else 1
    except Exception as e:  # noqa: BLE001
        print(f"  例外: {type(e).__name__}: {e}")
        return 1
    finally:
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    arg = (sys.argv[1] if len(sys.argv) > 1 else "test").strip().lower()
    if arg not in ("test", "prod"):
        print("用法: python scripts/capital_login_probe.py [test|prod]")
        raise SystemExit(2)
    raise SystemExit(main(arg))
