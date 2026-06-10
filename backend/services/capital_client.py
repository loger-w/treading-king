"""群益下單單例 —— 一條專屬 COM 執行緒(訊息幫浦 + 命令佇列),橋回 asyncio。

所有群益呼叫都在 _run() 那條執行緒上(COM apartment 親和性)。
送單前一律過安全閘 + 稽核。富邦完全不碰。
"""
from __future__ import annotations
import asyncio
import logging
import queue
import threading
from pathlib import Path

from services.capital_com import CapitalCom
from services.capital_mapping import to_stockorder_fields
from services.capital_models import (
    StockOrderRequest, OrderResult,
    CancelOrderRequest, CorrectPriceRequest, DecreaseQtyRequest,
)
from services.capital_safety import (
    SafetyConfig, check_stock_order, check_cancel, check_correct_price, check_decrease,
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
        self.store.apply_reply(rec)
        if self._broadcast and rec.seq_no:
            self._broadcast({"event": "capital_order", "data": {
                "seq_no": rec.seq_no, "stock_no": rec.stock_no,
                "status_label": rec.status_label, "price": rec.price, "qty": rec.qty,
            }})

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._thread = threading.Thread(target=self._run, daemon=True, name="capital-com")
        self._thread.start()

    def _init_com(self) -> bool:
        """登入 + 憑證 + 連回報主機。成功回 True、status=ok;失敗回 False、status=error。"""
        try:
            self._com.setup(self._handle_reply)
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

    async def _execute_write(self, *, action: str, req, gate, com_call) -> OrderResult:
        """寫入操作共用骨架:閘 → ready 檢查 → COM 佇列 → 稽核。"""
        if not gate.allowed:
            capital_audit.write(self._audit_path, env=self._env, req=req,
                                blocked=gate.reason, action=action)
            return OrderResult(ok=False, code=-1, message=gate.reason)
        if self._status != "ok" or self._loop is None:
            return OrderResult(ok=False, code=-1, message="群益未就緒(尚未登入或憑證失敗)")

        fut: asyncio.Future = self._loop.create_future()
        self._cmd_q.put((com_call, fut))
        message, code = await fut
        result = OrderResult(
            ok=(code == 0),
            code=code,
            message=f"{self._com.return_code_message(code)} {message}".strip(),
        )
        capital_audit.write(self._audit_path, env=self._env, req=req,
                            result=result, action=action)
        return result

    async def submit_stock_order(self, req: StockOrderRequest) -> OrderResult:
        def _do() -> tuple[str, int]:
            fields = to_stockorder_fields(req, self._full_account)
            return self._com.send_stock_order(self._user_id, fields)

        return await self._execute_write(
            action="order", req=req,
            gate=check_stock_order(req, self._safety), com_call=_do)

    async def cancel_stock_order(self, req: CancelOrderRequest) -> OrderResult:
        def _do() -> tuple[str, int]:
            return self._com.cancel_order(self._user_id, self._full_account, req.seq_no)

        return await self._execute_write(
            action="cancel", req=req,
            gate=check_cancel(self._safety), com_call=_do)

    async def correct_stock_price(self, req: CorrectPriceRequest) -> OrderResult:
        remaining = self.store.remaining_shares(req.seq_no)
        if remaining is None:
            return OrderResult(ok=False, code=-1, message=f"找不到委託 {req.seq_no}")

        def _do() -> tuple[str, int]:
            return self._com.correct_price(self._user_id, self._full_account, req.seq_no, req.price)

        return await self._execute_write(
            action="correct_price", req=req,
            gate=check_correct_price(req.price, remaining, self._safety), com_call=_do)

    async def decrease_stock_qty(self, req: DecreaseQtyRequest) -> OrderResult:
        def _do() -> tuple[str, int]:
            return self._com.decrease_qty(self._user_id, self._full_account, req.seq_no, req.qty)

        return await self._execute_write(
            action="decrease", req=req,
            gate=check_decrease(req.qty, self._safety), com_call=_do)
