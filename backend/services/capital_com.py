"""群益 COM 封裝。CapitalCom 是介面;SkcomCapitalCom 是真實 comtypes 實作。

真實實作的所有方法都必須在「同一條」CoInitialize 過的執行緒上呼叫
(COM apartment 親和性)—— 由 CapitalClient 的專屬執行緒保證。
"""
from __future__ import annotations

import os
from typing import Callable, Protocol


class CapitalCom(Protocol):
    def setup(self, on_reply: "Callable[[str], None] | None" = None) -> None: ...
    def set_authority(self, flag: int) -> int: ...        # 0=正式 2=測試
    def login(self, user_id: str, password: str) -> int: ...
    def init_order(self) -> int: ...
    def read_cert(self, user_id: str) -> int: ...
    def send_stock_order(self, user_id: str, fields: dict) -> tuple[str, int]: ...
    def return_code_message(self, code: int) -> str: ...
    def pump(self) -> None: ...


def _resolve_skcom_load(dll_dir: str | None) -> tuple[str | None, str]:
    """決定 SKCOM.dll 載入方式 → (要加進 DLL 搜尋路徑的資料夾 or None, 給 GetModule 的引數)。

    有設 dll_dir → 絕對路徑載入(穩,不靠行程 CWD/PATH,且把元件資料夾加進搜尋路徑,
    SKCOM.dll 的相依 DLL 才載得到);沒設(None/空白)→ 裸檔名,沿用舊行為。
    """
    d = (dll_dir or "").strip()
    if not d:
        return None, "SKCOM.dll"
    return d, os.path.join(d, "SKCOM.dll")


class SkcomCapitalCom:
    """真實群益 SKCOM 實作(comtypes)。"""

    def __init__(self, dll_dir: str | None = None) -> None:
        self._dll_dir = dll_dir
        self._dll_cookie = None      # os.add_dll_directory handle,存著避免被 GC 後移除路徑
        self._reply_sink = None
        self._reply_conn = None      # GetEvents advise 連線,存著避免被 GC → Unadvise
        self._sk = None
        self._center = None
        self._order = None
        self._reply = None

    def setup(self, on_reply: "Callable[[str], None] | None" = None) -> None:
        import comtypes.client
        add_dir, module_arg = _resolve_skcom_load(self._dll_dir)
        if add_dir:
            if not os.path.isdir(add_dir):
                raise FileNotFoundError(f"CAPITAL_DLL_DIR 不存在: {add_dir}")
            # Python 3.8+ 安全 DLL 搜尋:把元件資料夾加進去,SKCOM.dll 的相依(SKWebCALib 等)才載得到
            self._dll_cookie = os.add_dll_directory(add_dir)
        comtypes.client.GetModule(module_arg)
        import comtypes.gen.SKCOMLib as sk
        self._sk = sk
        self._center = comtypes.client.CreateObject(sk.SKCenterLib, interface=sk.ISKCenterLib)
        self._order = comtypes.client.CreateObject(sk.SKOrderLib, interface=sk.ISKOrderLib)
        self._reply = comtypes.client.CreateObject(sk.SKReplyLib, interface=sk.ISKReplyLib)
        # OnReplyMessage 回 -1 抑制群益彈窗(spec §4.6);OnNewData 主動回報轉給 on_reply。
        # sink 與 advise 連線都存住:丟掉會被 GC → Unadvise,登入即報
        # SK_WARNING_REGISTER_REPLYLIB_ONREPLYMESSAGE_FIRST。
        self._reply_sink = _ReplyEvents(on_reply)
        self._reply_conn = comtypes.client.GetEvents(self._reply, self._reply_sink)

    def set_authority(self, flag: int) -> int:
        return self._center.SKCenterLib_SetAuthority(flag)

    def login(self, user_id: str, password: str) -> int:
        return self._center.SKCenterLib_Login(user_id, password)

    def init_order(self) -> int:
        return self._order.SKOrderLib_Initialize()

    def read_cert(self, user_id: str) -> int:
        return self._order.ReadCertByID(user_id)

    def send_stock_order(self, user_id: str, fields: dict) -> tuple[str, int]:
        order = self._sk.STOCKORDER()
        for k, v in fields.items():
            setattr(order, k, v)
        # bAsync=0 同步,回 (message, nCode)
        message, code = self._order.SendStockOrder(user_id, 0, order)
        return message, code

    def return_code_message(self, code: int) -> str:
        return self._center.SKCenterLib_GetReturnCodeMessage(code)

    def pump(self) -> None:
        import pythoncom
        pythoncom.PumpWaitingMessages()


class _ReplyEvents:
    def __init__(self, on_reply: "Callable[[str], None] | None" = None) -> None:
        self._on_reply = on_reply

    def OnReplyMessage(self, bstrUserID, bstrMessage):
        return -1  # 群益慣例:回 -1 抑制彈窗

    def OnNewData(self, bstrUserID, bstrData):
        # 主動回報(委託/成交)轉給 client;回呼自身的例外不可炸掉 COM 事件迴圈
        if self._on_reply:
            try:
                self._on_reply(bstrData)
            except Exception:
                pass
