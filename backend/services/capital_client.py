"""群益下單單例 —— 一條專屬 COM 執行緒(訊息幫浦 + 命令佇列),橋回 asyncio。

所有群益呼叫都在 _run() 那條執行緒上(COM apartment 親和性)。
送單前一律過安全閘 + 稽核。富邦完全不碰。
"""
from __future__ import annotations
import asyncio
import logging
import queue
import threading
import time
from pathlib import Path

from services.capital_balance import BalanceCollector
from services.capital_close import build_close_order
from services.capital_com import CapitalCom
from services.capital_mapping import to_stockorder_fields
from services.capital_models import (
    StockOrderRequest, OrderResult, SEC_MARKETS,
    CancelOrderRequest, CorrectPriceRequest, DecreaseQtyRequest, PositionCloseRequest,
)
from services.capital_safety import (
    SafetyConfig, GateResult,
    check_stock_order, check_master, check_cancel, check_correct_price, check_decrease,
)
from services.capital_store import CapitalStore
from services import capital_audit

logger = logging.getLogger(__name__)


class CapitalClient:
    def __init__(
        self,
        com: CapitalCom,
        *,
        user_id: str,
        password: str,
        full_account: str,
        env: str,
        safety: SafetyConfig,
        audit_path: Path | None,
    ) -> None:
        self._com = com
        self._user_id = user_id
        self._password = password
        self._full_account = full_account
        self._env = env
        self._safety = safety
        self._audit_path = audit_path or capital_audit.DEFAULT_PATH
        self._cmd_q: "queue.Queue" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._status = "error"      # error | ok | degraded
        self._last_error: str | None = None
        self.store = CapitalStore()      # 委託/部位快取:回報事件寫入、REST 讀
        self._broadcast = None           # Callable[[dict], None] | None;由 app 注入推 WS
        self._balance = BalanceCollector(on_complete=self._on_balance_complete)
        self._balance_due: float | None = None   # monotonic;成交後 debounce 重查
        self._balance_last_ts: float = 0.0        # 定時重查用(0=啟動後第一圈就查)

    @property
    def status(self) -> str:
        return self._status

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def set_broadcast(self, fn) -> None:
        """fn(payload: dict) —— 由 app 注入,通常包成 call_soon_threadsafe + broadcaster。"""
        self._broadcast = fn

    def _handle_reply(self, bstr_data: str) -> None:
        """收到 OnNewData 主動回報 → 更新 store + 推 WS。在 COM 執行緒上被呼叫。"""
        from services.capital_reply import parse_onnewdata
        rec = parse_onnewdata(bstr_data)
        logger.info("Capital reply: seq=%s stock=%s status=%s qty=%s", rec.seq_no, rec.stock_no, rec.status_label, rec.qty)
        if rec.alt_seq_no and rec.seq_no and rec.alt_seq_no != rec.seq_no:
            # 真實樣本:預約單 KeyNo(idx0)≠ 尾欄序號(idx47),盤中單兩者相同。
            # 刪/改/減目前傳 KeyNo;首測預約單若回「查無委託」,改試這個尾欄值。
            logger.warning("Capital reply: KeyNo=%s 尾欄序號=%s 不同(預約單?)", rec.seq_no, rec.alt_seq_no)
        self.store.apply_reply(rec)
        if rec.status_raw == "D":      # 成交 → 排程庫存重查(debounce:連續成交只查尾端一次)
            self._mark_balance_dirty()
        if self._broadcast and rec.seq_no:
            self._broadcast({"event": "capital_order", "data": {
                "seq_no": rec.seq_no, "stock_no": rec.stock_no,
                "status_label": rec.status_label, "price": rec.price, "qty": rec.qty,
            }})

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._thread = threading.Thread(target=self._run, daemon=True, name="capital-com")
        self._thread.start()

    def _handle_balance(self, raw: str) -> None:
        """OnRealBalanceReport 事件(COM 執行緒)。"""
        self._balance.feed(raw)

    def _on_balance_complete(self, positions) -> None:
        self.store.set_positions(positions)
        self._balance_last_ts = time.monotonic()
        if self._broadcast:
            self._broadcast({"event": "capital_position", "data": {"count": len(positions)}})

    def _mark_balance_dirty(self, delay_s: float = 2.0) -> None:
        """成交回報後排程重查(debounce:連續成交只查尾端一次)。"""
        self._balance_due = time.monotonic() + delay_s

    def _maybe_query_balance(self) -> None:
        """_run 幫浦圈呼叫(COM 執行緒):due 到了或距上次查詢逾 60s → 發查詢。"""
        if self._status != "ok":
            return
        now = time.monotonic()
        due = self._balance_due is not None and now >= self._balance_due
        stale = now - self._balance_last_ts >= 60.0
        if not due and not stale:
            return
        self._balance_due = None
        self._balance_last_ts = now                 # 先記,失敗也不連發
        self._balance.reset()
        rc = self._com.get_real_balance(self._user_id, self._full_account)
        if rc != 0:
            logger.warning("GetRealBalanceReport rc=%s: %s", rc, self._com.return_code_message(rc))

    def _init_com(self) -> bool:
        """登入 + 憑證 + 連回報主機。成功回 True、status=ok;失敗回 False、status=error。"""
        try:
            self._com.setup(self._handle_reply, self._handle_balance)
            self._com.set_authority(2 if self._env == "test" else 0)
            code = self._com.login(self._user_id, self._password)
            if code != 0:
                raise RuntimeError("Login: " + self._com.return_code_message(code))
            self._com.init_order()
            code = self._com.read_cert(self._user_id)
            if code != 0:
                raise RuntimeError("ReadCertByID: " + self._com.return_code_message(code))
            # 連回報主機:漏這步 OnNewData 永遠不推,委託/成交/刪單回報全收不到。
            # 失敗不擋送單(送單獨立可用),只記警告。
            rc = self._com.connect_reply(self._user_id)
            if rc != 0:
                self._last_error = "回報連線失敗: " + self._com.return_code_message(rc)
                logger.warning("Capital reply connect failed (rc=%s); 送單可用但收不到回報", rc)
            self._status = "ok"
            logger.info("Capital login + cert OK (env=%s)", self._env)
            return True
        except Exception as e:  # noqa: BLE001
            self._status = "error"
            self._last_error = f"{type(e).__name__}: {e}"
            logger.error("Capital init failed: %s", self._last_error)
            return False

    def _run(self) -> None:
        import pythoncom
        pythoncom.CoInitialize()
        if not self._init_com():
            return

        while True:
            self._com.pump()
            self._balance.poll()           # 沒等到結束標記的 flush 保險
            self._maybe_query_balance()    # 成交後 debounce / 60s 定時重查
            try:
                cmd = self._cmd_q.get(timeout=0.05)
            except queue.Empty:
                continue
            if cmd is None:
                break
            fn, fut = cmd
            try:
                result = fn()
                self._loop.call_soon_threadsafe(fut.set_result, result)
            except Exception as e:  # noqa: BLE001
                self._loop.call_soon_threadsafe(fut.set_exception, e)

    def _write_gate(self, seq_no: str, base: GateResult) -> GateResult:
        """基本閘(總開關等)先過,再擋已知非證券市場的單。v1 寫入鏈只支援證券:
        期貨口數會讓改價金額閘(價×量)低估數十倍名目曝險,證券帳號打期貨序號行為也未定。
        查無此單/缺市場別寬鬆放行 — 刪單/減量是降風險操作,store 漏接時保留逃生口。"""
        if not base.allowed:
            return base
        m = self.store.market_of(seq_no)
        if m is not None and m not in SEC_MARKETS:
            return GateResult(False, f"非證券委託({m}),此端點僅支援證券")
        return base

    def _audit_after_send(self, req, result: OrderResult, action: str) -> None:
        """命令已出手後的稽核:寫檔失敗只能記 log,不可把已送進群益的單回報成失敗(誘發重送)。"""
        try:
            capital_audit.write(self._audit_path, env=self._env, req=req,
                                result=result, action=action)
        except Exception:  # noqa: BLE001
            logger.exception("稽核寫入失敗(action=%s)— 委託已送出,結果未入帳: %s", action, result)

    async def _execute_write(self, *, action: str, req, gate, com_call) -> OrderResult:
        """寫入操作共用骨架:閘 → ready 檢查 → COM 佇列 → 稽核。
        所有「拒絕/失敗」路徑都留稽核 —— 真錢寫入,事後要能查帳。
        送 COM 前稽核寫不進去就讓請求炸掉(錢還沒動,寧可不送);送出後相反,見 _audit_after_send。"""
        if not gate.allowed:
            capital_audit.write(self._audit_path, env=self._env, req=req,
                                blocked=gate.reason, action=action)
            return OrderResult(ok=False, code=-1, message=gate.reason)
        if self._status != "ok" or self._loop is None:
            reason = "群益未就緒(尚未登入或憑證失敗)"
            capital_audit.write(self._audit_path, env=self._env, req=req,
                                blocked=reason, action=action)
            return OrderResult(ok=False, code=-1, message=reason)

        fut: asyncio.Future = self._loop.create_future()
        self._cmd_q.put((com_call, fut))
        try:
            message, code = await fut
        except Exception as e:  # noqa: BLE001 — COM 例外轉 result,稽核不可斷
            result = OrderResult(ok=False, code=-1, message=f"COM 例外: {type(e).__name__}: {e}")
            self._audit_after_send(req, result, action)
            return result
        result = OrderResult(
            ok=(code == 0),
            code=code,
            message=f"{self._com.return_code_message(code)} {message}".strip(),
        )
        self._audit_after_send(req, result, action)
        return result

    async def submit_stock_order(self, req: StockOrderRequest, *, action: str = "order") -> OrderResult:
        def _do() -> tuple[str, int]:
            fields = to_stockorder_fields(req, self._full_account)
            return self._com.send_stock_order(self._user_id, fields)

        return await self._execute_write(
            action=action, req=req,
            gate=check_stock_order(req, self._safety), com_call=_do)

    async def close_position(self, req: PositionCloseRequest) -> OrderResult:
        pos = self.store.position_for(req.stock_no)
        if pos is None or pos.qty == 0:
            reason = f"{req.stock_no} 無部位可平"
            capital_audit.write(self._audit_path, env=self._env, req=req, blocked=reason, action="close")
            return OrderResult(ok=False, code=-1, message=reason)
        try:
            # v1 部位來源=現股報表 → pos_kind 恆 "cash"。信用部位資料接上後,改從部位資料帶種類。
            order = build_close_order(pos, req, pos_kind="cash")
        except ValueError as e:
            capital_audit.write(self._audit_path, env=self._env, req=req, blocked=str(e), action="close")
            return OrderResult(ok=False, code=-1, message=str(e))
        return await self.submit_stock_order(order, action="close")

    async def cancel_stock_order(self, req: CancelOrderRequest) -> OrderResult:
        def _do() -> tuple[str, int]:
            return self._com.cancel_order(self._user_id, self._full_account, req.seq_no)

        return await self._execute_write(
            action="cancel", req=req,
            gate=self._write_gate(req.seq_no, check_cancel(self._safety)), com_call=_do)

    async def correct_stock_price(self, req: CorrectPriceRequest) -> OrderResult:
        def _do() -> tuple[str, int]:
            return self._com.correct_price(self._user_id, self._full_account, req.seq_no, req.price)

        # 總開關/市場閘先於 store 查找:稽核 blocked 要記真正的擋單原因,
        # 總開關關閉時不可被「找不到委託」遮蔽(其餘寫入也都是總開關最先)。
        pre = self._write_gate(req.seq_no, check_master(self._safety))
        if not pre.allowed:
            return await self._execute_write(action="correct_price", req=req, gate=pre, com_call=_do)
        remaining = self.store.remaining_shares(req.seq_no)
        if remaining is None:
            reason = f"找不到委託 {req.seq_no}"
            capital_audit.write(self._audit_path, env=self._env, req=req,
                                blocked=reason, action="correct_price")
            return OrderResult(ok=False, code=-1, message=reason)

        return await self._execute_write(
            action="correct_price", req=req,
            gate=check_correct_price(req.price, remaining, self._safety), com_call=_do)

    async def decrease_stock_qty(self, req: DecreaseQtyRequest) -> OrderResult:
        def _do() -> tuple[str, int]:
            return self._com.decrease_qty(self._user_id, self._full_account, req.seq_no, req.qty)

        return await self._execute_write(
            action="decrease", req=req,
            gate=self._write_gate(req.seq_no, check_decrease(req.qty, self._safety)), com_call=_do)
