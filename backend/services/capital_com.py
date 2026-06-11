"""群益 COM 封裝。CapitalCom 是介面;SkcomCapitalCom 是真實 comtypes 實作。

真實實作的所有方法都必須在「同一條」CoInitialize 過的執行緒上呼叫
(COM apartment 親和性)—— 由 CapitalClient 的專屬執行緒保證。
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Protocol

logger = logging.getLogger(__name__)


class CapitalCom(Protocol):
    def setup(self, on_reply: "Callable[[str], None] | None" = None,
              on_balance: "Callable[[str], None] | None" = None,
              on_profit: "Callable[[str], None] | None" = None,
              on_reply_disconnect: "Callable[[int], None] | None" = None) -> None: ...
    def set_authority(self, flag: int) -> int: ...        # 0=正式 2=測試
    def login(self, user_id: str, password: str) -> int: ...
    def init_order(self) -> int: ...
    def read_cert(self, user_id: str) -> int: ...
    def connect_reply(self, user_id: str) -> int: ...   # 連回報主機,OnNewData 才會推
    def send_stock_order(self, user_id: str, fields: dict) -> tuple[str, int]: ...
    def cancel_order(self, user_id: str, full_account: str, seq_no: str) -> tuple[str, int]: ...
    def correct_price(self, user_id: str, full_account: str, seq_no: str, price: float) -> tuple[str, int]: ...
    def decrease_qty(self, user_id: str, full_account: str, seq_no: str, qty: int) -> tuple[str, int]: ...
    def get_real_balance(self, user_id: str, full_account: str) -> int: ...  # 結果走 OnRealBalanceReport 事件
    def get_profit_loss_gw(self, user_id: str, full_account: str) -> int: ...  # 結果走 OnProfitLossGWReport
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
        self._order_sink = None      # OrderLib 事件(即時庫存),同樣要存住防 GC Unadvise
        self._order_conn = None
        self._sk = None
        self._center = None
        self._order = None
        self._reply = None

    def setup(self, on_reply: "Callable[[str], None] | None" = None,
              on_balance: "Callable[[str], None] | None" = None,
              on_profit: "Callable[[str], None] | None" = None,
              on_reply_disconnect: "Callable[[int], None] | None" = None) -> None:
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
        self._reply_sink = _ReplyEvents(on_reply, on_disconnect=on_reply_disconnect)
        self._reply_conn = comtypes.client.GetEvents(self._reply, self._reply_sink)
        self._order_sink = _OrderEvents(on_balance, on_profit)
        self._order_conn = comtypes.client.GetEvents(self._order, self._order_sink)

    def set_authority(self, flag: int) -> int:
        return self._center.SKCenterLib_SetAuthority(flag)

    def login(self, user_id: str, password: str) -> int:
        return self._center.SKCenterLib_Login(user_id, password)

    def init_order(self) -> int:
        return self._order.SKOrderLib_Initialize()

    def read_cert(self, user_id: str) -> int:
        return self._order.ReadCertByID(user_id)

    def connect_reply(self, user_id: str) -> int:
        # 連上回報主機後,OnNewData 才會推委託/成交/刪單回報(並重播當日 backlog)。
        return self._reply.SKReplyLib_ConnectByID(user_id)

    def send_stock_order(self, user_id: str, fields: dict) -> tuple[str, int]:
        order = self._sk.STOCKORDER()
        for k, v in fields.items():
            setattr(order, k, v)
        # bAsync=0 同步,回 (message, nCode)
        message, code = self._order.SendStockOrder(user_id, 0, order)
        return message, code

    def cancel_order(self, user_id: str, full_account: str, seq_no: str) -> tuple[str, int]:
        message, code = self._order.CancelOrderBySeqNo(user_id, 0, full_account, seq_no)
        return message, code

    def correct_price(self, user_id: str, full_account: str, seq_no: str, price: float) -> tuple[str, int]:
        # 末參數 nTradeType=0(ROD),同官方範例;價格字串化 %.2f 與送單一致
        message, code = self._order.CorrectPriceBySeqNo(user_id, 0, full_account, seq_no, f"{price:.2f}", 0)
        return message, code

    def decrease_qty(self, user_id: str, full_account: str, seq_no: str, qty: int) -> tuple[str, int]:
        # qty 單位=張(與 SendStockOrder.nQty 同慣例;首次實測對群益 App 驗)
        message, code = self._order.DecreaseOrderBySeqNo(user_id, 0, full_account, seq_no, qty)
        return message, code

    def get_real_balance(self, user_id: str, full_account: str) -> int:
        # 非同步查詢:nCode 同步回,結果走 OnRealBalanceReport 事件
        return self._order.GetRealBalanceReport(user_id, full_account)

    def get_profit_loss_gw(self, user_id: str, full_account: str) -> int:
        # 未實現損益試算(彙總、全部商品);結果走 OnProfitLossGWReport 事件。
        # 字串欄一律帶空字串 — comtypes 未設的 BSTR 是 None,群益端行為未定義
        q = self._sk.TSPROFITLOSSGWQUERY()
        q.bstrFullAccount = full_account
        q.nTPQueryType = 0     # 0=未實現
        q.nFunc = 0            # 0=彙總
        q.bstrStockNo = ""
        q.bstrTradeType = ""
        q.bstrStartDate = ""
        q.bstrEndDate = ""
        q.bstrBookNo = ""
        q.bstrSeqNo = ""
        return self._order.GetProfitLossGWReport(user_id, q)

    def return_code_message(self, code: int) -> str:
        return self._center.SKCenterLib_GetReturnCodeMessage(code)

    def pump(self) -> None:
        import pythoncom
        pythoncom.PumpWaitingMessages()


class _ReplyEvents:
    def __init__(self, on_reply: "Callable[[str], None] | None" = None,
                 on_disconnect: "Callable[[int], None] | None" = None) -> None:
        self._on_reply = on_reply
        self._on_disconnect = on_disconnect

    def OnReplyMessage(self, bstrUserID, bstrMessage):
        return -1  # 群益慣例:回 -1 抑制彈窗

    def OnConnect(self, bstrUserID, nErrorCode):
        # 回報主機連線結果;0=成功,之後 OnNewData 才會推(含當日 backlog)。
        if nErrorCode == 0:
            logger.info("Capital reply connected (user=%s)", bstrUserID)
        else:
            logger.warning("Capital reply connect error (user=%s, code=%s)", bstrUserID, nErrorCode)

    def OnDisconnect(self, bstrUserID, nErrorCode):
        # 回報主機斷線(事件簽名出自 comtypes gen 的 _ISKReplyLibEvents;
        # comtypes 對 sink 未實作的事件靜默忽略 → 不掛這個 handler 就完全偵測不到)。
        # 只做偵測+通知降級;自動重連需先 store.clear() 防成交重複累計,另案處理。
        logger.error("Capital reply disconnected (user=%s, code=%s)", bstrUserID, nErrorCode)
        if self._on_disconnect:
            try:
                self._on_disconnect(nErrorCode)
            except Exception:
                logger.exception("reply 斷線回呼例外(已忽略,COM 事件迴圈不可炸)")

    def OnNewData(self, bstrUserID, bstrData):
        # 主動回報(委託/成交)轉給 client;回呼自身的例外不可炸掉 COM 事件迴圈,
        # 但必須留痕 — 這代表一筆委託/成交回報被丟棄,委託面板會跟市場脫節
        if self._on_reply:
            try:
                self._on_reply(bstrData)
            except Exception:
                logger.exception("reply 回呼例外,該筆回報丟棄: %r", bstrData)


class _OrderEvents:
    """SKOrderLib 事件 sink(即時庫存+損益試算);回呼例外不可炸掉 COM 事件迴圈。"""

    def __init__(self, on_balance: "Callable[[str], None] | None" = None,
                 on_profit: "Callable[[str], None] | None" = None) -> None:
        self._on_balance = on_balance
        self._on_profit = on_profit

    def OnRealBalanceReport(self, bstrData):
        if self._on_balance:
            try:
                self._on_balance(bstrData)
            except Exception:
                logger.exception("balance 回呼例外,該筆庫存事件丟棄: %r", bstrData)

    def OnProfitLossGWReport(self, bstrData):
        if self._on_profit:
            try:
                self._on_profit(bstrData)
            except Exception:
                logger.exception("profit 回呼例外,該筆損益事件丟棄: %r", bstrData)
