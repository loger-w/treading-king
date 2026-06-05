"""群益 COM 封裝。CapitalCom 是介面;SkcomCapitalCom 是真實 comtypes 實作。

真實實作的所有方法都必須在「同一條」CoInitialize 過的執行緒上呼叫
(COM apartment 親和性)—— 由 CapitalClient 的專屬執行緒保證。
"""
from __future__ import annotations
from typing import Protocol


class CapitalCom(Protocol):
    def setup(self) -> None: ...
    def set_authority(self, flag: int) -> int: ...        # 0=正式 2=測試
    def login(self, user_id: str, password: str) -> int: ...
    def init_order(self) -> int: ...
    def read_cert(self, user_id: str) -> int: ...
    def send_stock_order(self, user_id: str, fields: dict) -> tuple[str, int]: ...
    def return_code_message(self, code: int) -> str: ...
    def pump(self) -> None: ...


class SkcomCapitalCom:
    """真實群益 SKCOM 實作(comtypes)。"""

    def __init__(self) -> None:
        self._sk = None
        self._center = None
        self._order = None
        self._reply = None

    def setup(self) -> None:
        import comtypes.client
        comtypes.client.GetModule("SKCOM.dll")
        import comtypes.gen.SKCOMLib as sk
        self._sk = sk
        self._center = comtypes.client.CreateObject(sk.SKCenterLib, interface=sk.ISKCenterLib)
        self._order = comtypes.client.CreateObject(sk.SKOrderLib, interface=sk.ISKOrderLib)
        self._reply = comtypes.client.CreateObject(sk.SKReplyLib, interface=sk.ISKReplyLib)
        # OnReplyMessage 必須回 -1 抑制群益彈窗(spec §4.6)
        comtypes.client.GetEvents(self._reply, _ReplyEvents())

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
    def OnReplyMessage(self, bstrUserID, bstrMessage):
        return -1  # 群益慣例:回 -1 抑制彈窗
