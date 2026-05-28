# 監聽清單(Monitor List)+ 訊號 Discord 通知 — 設計文件

**日期**:2026-05-26
**範圍**:後端(`signal_engine`、新增 route、新 service、DB migration)+ 前端(新增 panel、`ActiveSignalEditor` 改動)+ 環境變數
**狀態**:設計完成,待寫實作計畫
**相關**:[即時訊號 schema](../../../backend/models/condition.py)、[既有 alerts](../../../backend/services/alerts.py)、[書籤 design](./2026-05-20-bookmarks-design.md)

---

## 1. 目的

兩個用戶需求合併處理:

1. **使用者可選取訊號要監聽哪幾檔股票**
   — 既有架構把「監聽範圍」綁在 `active_signals.scope`(`watchlist` 或 `symbols`),但 `scope=watchlist` 等同「所有自選書籤股票」,沒有「跨書籤、跨規則的單一監聽清單」概念;`scope=symbols` 後端 schema 有但前端缺 picker。
   — 用戶期望:**一份獨立的「監聽清單」,所有 active_signals 都套這份清單評估**。資料層跟「書籤」分離(可從書籤股票加入、搜尋加入,但不是書籤)。

2. **訊號觸發時推 Discord 通知**
   — 既有 `services/alerts.py` 有 Discord webhook,但只用於系統嚴重異常(WS 斷線、evaluator 過載)。訊號觸發目前只走「前端 WS broadcast + `signals_log` table」,**沒有任何即時推送**到使用者手機 / IM。
   — 用戶期望:每條規則一個 `notify_discord` 開關,觸發時打 Discord webhook;LINE 暫不做(LINE Notify 已於 2025/04 停用,Messaging API 設定門檻高,先 Discord)。

---

## 2. 範圍

### 2.1 第一版包含

- **DB schema**:新 table `monitor_list (user_label, symbol, added_at)`;`active_signals` 加 `notify_discord boolean default true`
- **後端 service**:新 `discord_notifier.py`(訊號推送專用,跟 alerts.py 分開但實作風格一致)
- **後端 routes**:新 `routes/monitor_list.py`(GET / POST / DELETE)
- **後端 signal_engine 改動**:scope 評估改成讀 `monitor_list`;`_fanout` 內加 Discord notify branch
- **前端**:新 `MonitorListPanel`(嵌入既有書籤面板 sidebar)、`useMonitorList` hook、`ActiveSignalEditor` 移除 scope 選擇 + 加通知 toggle、`IntradayChart` header 加「+ 加入監聽」按鈕、`AddToBookmarksDialog` 加「同時加入監聽清單」checkbox
- **環境變數**:新增 `SIGNALS_DISCORD_WEBHOOK_URL`(可與 `ALERTS_DISCORD_WEBHOOK_URL` 相同也可分流)
- **Migration backfill**:既有 user 的「自選」書籤股票 + 既有 `active_signals.scope.symbols` union 進 `monitor_list`,讓既有規則 migrate 後不掉訊號

### 2.2 不在這版 scope

- **LINE 通知**:LINE Messaging API 申請流程(channel、bot、userId)後續若有需求再做
- **通知 throttle / batching**:用戶決定信任既有 `cooldown_seconds` 機制;若實務上撞 Discord 429 太頻繁再加
- **多 webhook(per-rule webhook URL)**:單一 webhook 已足
- **per-rule scope override**:既有 `active_signals.scope` 欄位保留 schema(audit),程式碼一律 ignore,所有 rule 統一套監聽清單
- **MXF / 期貨訊號**:既有訊號引擎只跑股票,本案不擴
- **訊號通知格式深度自訂**:embed 內容用合理 default,日後再讓 user 客製

---

## 3. 需求清單(brainstorm 確認)

| 項目 | 決議 |
|---|---|
| 監聽範圍 | 全域監聽白名單(獨立 entity) |
| 跟書籤關係 | **完全獨立**;可從書籤股票加入 / 從搜尋加入 |
| 通知管道 | **Discord** 一個就好(LINE 暫不做) |
| 通知開關粒度 | **per-rule** `notify_discord boolean` |
| 通知 throttle | **不加**;依賴既有 `cooldown_seconds` |
| 既有 scope 處理 | **退場**;舊 scope=watchlist / scope=symbols 規則 auto-migrate(symbols union 進 monitor_list);所有 rule 一律套監聽清單 |
| Discord webhook 環境變數 | **新增 `SIGNALS_DISCORD_WEBHOOK_URL`** 跟 `ALERTS_DISCORD_WEBHOOK_URL` 分開,可同 URL 可分流 |

---

## 4. 整體架構

### 4.1 概念分層

```
┌─────────────────────────────────────────────────────────────┐
│  Bookmarks(UI / 我關心的股票)── 既有,不動                  │
│       ↓ 可選來源                                             │
│  Monitor List(訊號評估 universe)── 新增,獨立 table          │
│       ↓ 每條 rule 套用                                       │
│  Active Signals(評估規則 + 通知開關)── 加 notify_discord     │
│       ↓ 觸發                                                 │
│  ┌─ ws_broadcaster(前端推播)── 既有                         │
│  ├─ supabase_writer(signals_log)── 既有                     │
│  └─ DiscordNotifier(訊號專用)── 新增                        │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 Data flow:tick → 訊號 → 通知

```
富邦 WS tick
   │
   ▼
signal_engine.enqueue ─→ _consume_loop / _heartbeat_loop
                              │
                              ├─ 對每條 active_signal 跑條件
                              ├─ scope 改成「symbol 是否在 monitor_list」
                              ├─ cooldown 過了 → _fanout()
                              │
                              ▼
                       _fanout():
                          1. ws_broadcaster.broadcast(...)        ← 既有
                          2. supabase_writer.append(...)          ← 既有
                          3. if active.notify_discord:            ← 新增
                                discord_notifier.send_signal(...) ← 新增
```

### 4.3 WS subscribe ownership

WS pool refcount 是 multi-owner(同 symbol 多 owner,任一移除不影響其他)。本案新增 owner:

| Owner ID | 來源 | 觸發 |
|---|---|---|
| `bookmark:{gid}`(既有) | 各書籤 | 加 / 移除書籤股票 |
| `active_signal_id`(既有) | 各規則 scope | 規則建立 / 移除(本案後 scope 退場,但 owner 仍保留以利對齊既有 owner lifecycle) |
| `preview`(既有) | Monitor 頁選股預覽 | usePreviewSubscribe |
| **`monitor_list`(新)** | 監聽清單 | 加 / 移除監聽清單股票 |

`monitor_list` owner 是 user 全域 singleton — 一個 user 的所有監聽清單股票共用此 owner。

---

## 5. DB Schema

### 5.1 新檔:`supabase/migrations/0008_monitor_list_and_notify.sql`

```sql
-- ---------------------------------------------------------------------------
-- monitor_list:訊號評估的全域監聽 universe(per user_label)
-- ---------------------------------------------------------------------------
create table if not exists monitor_list (
  user_label text not null,
  symbol     text not null references symbols(symbol),
  added_at   timestamptz default now(),
  primary key (user_label, symbol)
);

create index if not exists idx_monitor_list_label on monitor_list(user_label);

alter table monitor_list enable row level security;

create policy "anon can read monitor_list"
  on monitor_list for select
  to anon, authenticated
  using (true);

-- ---------------------------------------------------------------------------
-- active_signals:per-rule 通知開關
-- ---------------------------------------------------------------------------
alter table active_signals
  add column if not exists notify_discord boolean not null default true;

-- ---------------------------------------------------------------------------
-- Backfill 1:每個 user 的「自選」書籤股票 → monitor_list
--   (舊 scope=watchlist 等於這份,migrate 後語意不變)
-- ---------------------------------------------------------------------------
insert into monitor_list (user_label, symbol)
select distinct bg.user_label, wi.symbol
from watchlist_items wi
join bookmark_groups bg on bg.id = wi.group_id
where bg.user_label is not null
  and bg.name = '自選'
on conflict do nothing;

-- ---------------------------------------------------------------------------
-- Backfill 2:舊 active_signals.scope.symbols (scope=symbols) → monitor_list
--   解 union,讓舊 rule 改成「全套監聽清單」後仍能命中原本要監聽的標的。
--   exists 子查詢確保被加入的 symbol 還在 symbols 表(避免 FK 撞 delisted)。
-- ---------------------------------------------------------------------------
insert into monitor_list (user_label, symbol)
select distinct a.user_label, sym.symbol
from active_signals a,
     lateral jsonb_array_elements_text(a.scope->'symbols') as sym(symbol)
where a.scope->>'type' = 'symbols'
  and jsonb_typeof(a.scope->'symbols') = 'array'
  and exists (select 1 from symbols s where s.symbol = sym.symbol)
on conflict do nothing;
```

### 5.2 Rollback 策略

```sql
-- 萬一要回退:
drop table if exists monitor_list cascade;
alter table active_signals drop column if exists notify_discord;
-- scope 欄位本就沒動,程式碼 revert 後恢復原行為
```

---

## 6. Backend changes

### 6.1 新檔:`backend/services/discord_notifier.py`

```python
"""Discord notifier — 訊號觸發推送(跟 alerts.py 的系統異常 webhook 分開)。"""
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_WEBHOOK_URL: str | None = None


def _get_webhook_url() -> str | None:
    global _WEBHOOK_URL
    if _WEBHOOK_URL is None:
        _WEBHOOK_URL = os.getenv("SIGNALS_DISCORD_WEBHOOK_URL", "").strip() or ""
    return _WEBHOOK_URL or None


async def send_signal(
    *,
    rule_name: str,
    symbol: str,
    price: float,
    volume: int,
    triggered_at_iso: str,
    cdp_touch: dict | None = None,
    ma_touch: dict | None = None,
) -> None:
    """訊號觸發推 Discord;失敗 silent log(不影響主流程)。"""
    url = _get_webhook_url()
    if not url:
        return

    fields: list[dict[str, Any]] = [
        {"name": "代號", "value": symbol, "inline": True},
        {"name": "價格", "value": f"{price:.2f}", "inline": True},
        {"name": "量", "value": str(volume), "inline": True},
    ]
    if cdp_touch:
        fields.append({
            "name": "CDP",
            "value": f"{cdp_touch['level']} ({cdp_touch.get('role', 'touch')})",
            "inline": True,
        })
    if ma_touch:
        fields.append({
            "name": "MA",
            "value": f"{ma_touch['level']} ({ma_touch.get('role', 'touch')})",
            "inline": True,
        })

    embed = {
        "title": f"📈 {rule_name}",
        "description": f"`{symbol}` 觸發",
        "color": 0x32D27C,
        "fields": fields,
        "timestamp": triggered_at_iso,
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json={"embeds": [embed]})
    except Exception as e:
        logger.warning("Discord signal notify failed: %s", e)
```

### 6.2 改動:`backend/services/signal_engine.py`

**Scope 評估邏輯切換 — 從 `active.scope` 改讀 `monitor_list`**

關鍵 method:

```python
async def _load_monitor_symbols(self) -> set[str]:
    """從 monitor_list 拉本 user 的所有監聽 symbol。"""
    sb = get_supabase()
    if sb.client is None:
        return set()
    res = await asyncio.to_thread(
        lambda: sb.client.table("monitor_list")
        .select("symbol")
        .eq("user_label", get_user_label())
        .execute()
    )
    return {r["symbol"] for r in (res.data or [])}


async def _refill_field_cache(self) -> None:
    """改成 iterate monitor_list,不再 iterate active.scope。"""
    symbols_needed: set[str] = await self._load_monitor_symbols()
    # 後續 cdp / sma refill 邏輯不變
    cdp = get_cdp_service()
    for sym in symbols_needed:
        levels = await cdp.get(sym)
        if levels:
            d = self._field_cache.setdefault(sym, {})
            d["cdp_ah"] = levels["ah"]
            # ... 同既有
    for sym in symbols_needed:
        sma_5, sma_20 = await ma_service.fetch_sma_5_20(sym)
        # ... 同既有
    self._day_volume.clear()
    self._last_field_refill_date = date.today()


def _scope_includes(self, active: ActiveSignalOut, symbol: str) -> bool:
    """所有 rule 共用 monitor_list;field_cache 的 key 就是 monitor_list 的 union。"""
    return symbol in self._field_cache


def _scope_symbols(self, active: ActiveSignalOut) -> list[str]:
    """heartbeat 用,回傳 monitor_list 全部 symbol。"""
    return list(self._field_cache.keys())
```

**`_fanout` 新增 Discord branch**

```python
async def _fanout(
    self, active: ActiveSignalOut, symbol: str, tick: Tick,
    cdp_touch: dict | None = None, ma_touch: dict | None = None,
) -> None:
    from services import discord_notifier
    from services.supabase_writer import get_supabase_writer

    data: dict = {
        "active_signal_id": active.id,
        "active_signal_name": active.name,
        "symbol": symbol,
        "triggered_at": datetime.now(timezone.utc).isoformat(),
        "trigger_price": tick.price,
        "trigger_volume": tick.size,
    }
    if cdp_touch: data["cdp_touch"] = cdp_touch
    if ma_touch:  data["ma_touch"]  = ma_touch

    # 1. 前端 WS broadcast(既有)
    await get_broadcaster().broadcast({"event": "signal", "data": data})

    # 2. supabase log(既有)
    context: dict = {"latest_tick_time": tick.time}
    if cdp_touch: context["cdp_touch"] = cdp_touch
    if ma_touch:  context["ma_touch"]  = ma_touch
    get_supabase_writer().append({
        "active_signal_id": active.id,
        "symbol": symbol,
        "trigger_price": tick.price,
        "trigger_volume": tick.size,
        "context_json": context,
        "user_label": get_user_label(),
    })

    # 3. Discord notify(新)— per-rule 開關,失敗不影響主流程
    if active.notify_discord:
        try:
            await discord_notifier.send_signal(
                rule_name=active.name,
                symbol=symbol,
                price=tick.price,
                volume=tick.size,
                triggered_at_iso=data["triggered_at"],
                cdp_touch=cdp_touch,
                ma_touch=ma_touch,
            )
        except Exception as e:
            logger.warning("discord notify failed: %s", e)
```

### 6.3 新檔:`backend/routes/monitor_list.py`

```python
"""GET/POST/DELETE /api/monitor_list — 監聽清單 CRUD。

POST 順手:
  - ws_pool.subscribe(owner='monitor_list')
  - cdp_service.backfill_from_fubon(symbol) 背景
  - signal_engine.refresh_active_signals()
DELETE 反過來 unsubscribe + refresh。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.cdp import get_cdp_service
from services.fubon_ws import get_ws_pool
from services.supabase_client import SupabaseStatus, get_supabase
from services.user_context import get_user_label

logger = logging.getLogger(__name__)
router = APIRouter()

OWNER_ID = "monitor_list"


class MonitorListAdd(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)


def _ensure_supabase():
    sb = get_supabase()
    if sb.status != SupabaseStatus.OK or sb.client is None:
        raise HTTPException(503, detail={"error": "supabase_unavailable"})
    return sb


@router.get("/api/monitor_list")
async def list_monitor() -> dict:
    sb = _ensure_supabase()
    res = await asyncio.to_thread(
        lambda: sb.client.table("monitor_list")
        .select("symbol, added_at, symbols(name, market, is_etf)")
        .eq("user_label", get_user_label())
        .order("added_at", desc=True)
        .execute()
    )
    rows = res.data or []
    out = []
    for r in rows:
        meta = r.get("symbols") or {}
        out.append({
            "symbol": r["symbol"],
            "added_at": r.get("added_at"),
            "name": meta.get("name"),
            "market": meta.get("market"),
            "is_etf": meta.get("is_etf"),
        })
    return {"items": out, "count": len(out)}


@router.post("/api/monitor_list", status_code=201)
async def add_monitor(payload: MonitorListAdd) -> dict:
    sb = _ensure_supabase()
    label = get_user_label()

    # symbol 必須存在 symbols 表
    sym_res = await asyncio.to_thread(
        lambda: sb.client.table("symbols").select("symbol")
        .eq("symbol", payload.symbol).limit(1).execute()
    )
    if not (sym_res.data or []):
        raise HTTPException(404, detail={"error": "symbol_not_found"})

    # 先試 ws subscribe;失敗就不寫 DB,避免狀態不一致
    try:
        await get_ws_pool().subscribe(payload.symbol, owner_id=OWNER_ID)
    except RuntimeError as e:
        raise HTTPException(503, detail={"error": "ws_capacity_full", "detail": str(e)})

    # 寫 DB
    try:
        await asyncio.to_thread(
            lambda: sb.client.table("monitor_list").insert({
                "user_label": label,
                "symbol": payload.symbol,
            }).execute()
        )
    except Exception as e:
        # 寫失敗 → rollback ws subscribe
        try:
            await get_ws_pool().unsubscribe(payload.symbol, owner_id=OWNER_ID)
        except Exception:
            pass
        raise HTTPException(409, detail={"error": "already_in_monitor_list", "detail": str(e)})

    # CDP backfill 背景跑
    asyncio.create_task(get_cdp_service().backfill_from_fubon(payload.symbol))

    # signal_engine refresh
    try:
        from services.signal_engine import get_signal_engine
        await get_signal_engine().refresh_active_signals()
    except Exception as e:
        logger.warning("monitor_list add: refresh signal_engine failed: %s", e)

    return {"symbol": payload.symbol, "status": "added"}


@router.delete("/api/monitor_list/{symbol}", status_code=204)
async def remove_monitor(symbol: str) -> None:
    sb = _ensure_supabase()
    await asyncio.to_thread(
        lambda: sb.client.table("monitor_list").delete()
        .eq("user_label", get_user_label())
        .eq("symbol", symbol)
        .execute()
    )
    try:
        await get_ws_pool().unsubscribe(symbol, owner_id=OWNER_ID)
    except Exception as e:
        logger.warning("monitor_list remove: ws unsubscribe failed: %s", e)
    try:
        from services.signal_engine import get_signal_engine
        await get_signal_engine().refresh_active_signals()
    except Exception as e:
        logger.warning("monitor_list remove: refresh signal_engine failed: %s", e)
    return None
```

### 6.4 改動:`backend/models/condition.py`

- `ActiveSignalCreate` 加 `notify_discord: bool = True`
- `Scope` / `WatchlistScope` / `SymbolsScope` **保留**(讓舊紀錄反序列化能過);新建時若 payload 未帶 scope,後端填一個 default `WatchlistScope`(欄位仍寫進 DB 當 audit,程式碼 ignore)

### 6.5 改動:`backend/routes/active_signals.py`

- `_scope_symbols` helper 可以拿掉(或保留但未使用)
- `create_active` / `update_active`:
  - `notify_discord` 進 insert / update payload
  - scope 從 payload 拿掉 / 後端 default 填入
  - 不再做 per-rule scope-based ws subscribe(因為 ws 訂閱由 `monitor_list` owner 統一管)
- `delete_active`:不再 unsubscribe(monitor_list owner 仍存在 → 該 symbol 的 ws 訂閱保留)

### 6.6 改動:`backend/main.py`

- startup 訂閱邏輯改動:
  - **保留**書籤訂閱(讓 BookmarksPanel / Monitor 頁報價照常運作)
  - **新增**:從 `monitor_list` 撈 user 的所有 symbol,owner=`monitor_list` 訂閱
- include 新 router:`app.include_router(monitor_list.router)`

### 6.7 改動:`backend/.env.example`

```
# 訊號觸發 Discord webhook(跟 ALERTS_DISCORD_WEBHOOK_URL 分開,可同 URL 也可分流)
SIGNALS_DISCORD_WEBHOOK_URL=
```

---

## 7. Frontend changes

### 7.1 改動:`frontend/src/lib/api.ts`

```typescript
export interface MonitorListItem {
  symbol: string;
  added_at: string;
  name: string | null;
  market: string | null;
  is_etf: boolean | null;
}

// ActiveSignal 加欄位
export interface ActiveSignal {
  // ... 既有 fields ...
  notify_discord: boolean;
}

// ActiveSignalCreate payload:scope 變 optional(後端 default 填)
// 新增 notify_discord

export const api = {
  // ... 既有 ...
  monitorList: {
    list:   () => fetchJSON<{ items: MonitorListItem[]; count: number }>("/api/monitor_list"),
    add:    (symbol: string) => fetchJSON("/api/monitor_list", { method: "POST", body: { symbol } }),
    remove: (symbol: string) => fetchJSON(`/api/monitor_list/${symbol}`, { method: "DELETE" }),
  },
};
```

### 7.2 新檔:`frontend/src/hooks/useMonitorList.ts`

沿用 `useWatchlist` / `useBookmarks` pattern:

```typescript
export function useMonitorList() {
  const [items, setItems] = useState<MonitorListItem[]>([]);
  const refresh = useCallback(async () => { /* api.monitorList.list */ }, []);
  const add     = useCallback(async (symbol: string) => { /* ... */ }, [refresh]);
  const remove  = useCallback(async (symbol: string) => { /* ... */ }, [refresh]);
  useEffect(() => { refresh(); }, [refresh]);
  return { items, refresh, add, remove };
}
```

### 7.3 改動:`frontend/src/components/ActiveSignalEditor.tsx`

- **移除**「套用範圍 / 自選清單全部 / 指定股票」整個區塊(scope state 也拿掉)
- Logic 列右邊**新增**「Discord 通知」toggle:

```
┌─ 邏輯 ─────┐  ┌─ Discord 通知 ───────┐  ┌─ Cooldown 秒 ─┐
│ ○ AND       │  │ [☑] 觸發時推送        │  │ [1800]         │
│ ○ OR        │  │                       │  │                │
└─────────────┘  └───────────────────────┘  └────────────────┘
```

- `save()` payload 拿掉 `scope`(後端 default 補)、加入 `notify_discord`
- 編輯既有 rule 時:`initial.notify_discord` 預填 toggle

### 7.4 新檔:`frontend/src/components/MonitorListPanel.tsx` 與 BookmarksPanel 整合

在 `BookmarksPanel` 的 sidebar(`130px` 寬欄)上方加一個常駐項「☆ 監聽(N)」:

```
┌── 書籤面板 ──────────────────────────┐
│ 書籤 (12)              ⚙ 管理        │
├──────────┬───────────────────────────┤
│ ☆ 監聽 5 │  2330  台積電  618  +1.2% │  ← 監聽清單 view
│ ─────────│  1101  台泥    36   -0.5% │
│ 全部 (8) │  ...                       │
│ 自選 (5) │                            │
│ 大漲 (3) │                            │
│  ＋新增  │                            │
└──────────┴───────────────────────────┘
```

當 sidebar 切到「監聽」時主區渲染 `MonitorListView`(視覺重用既有 `SingleListView` 的 ItemRow,但用 `useMonitorList()` 的 items + 提供「× 移除」按鈕);切到其他項時回到既有書籤行為。

**注意**:這不是「監聽清單變成書籤」 — 資料層仍是獨立 table,只是 UI 入口為了視覺一致借用 sidebar。

### 7.5 改動:`frontend/src/components/IntradayChart.tsx`

Header 既有「+ 加入自選 / 已在自選 ✓」按鈕擴成兩顆:

```
[+ 加入自選]  [+ 加入監聽]
[已在自選 ✓]  [已在監聽 ✓]
```

「+ 加入監聽」打 `api.monitorList.add(symbol)`;狀態判定接 `useMonitorList`。

### 7.6 改動:`frontend/src/components/AddToBookmarksDialog.tsx`

Dialog 底部加一個 checkbox:

```
[☐] 同時加入監聽清單(訊號評估)
```

打勾的話,送出時順手 `api.monitorList.add(symbol)`。

### 7.7 改動:`frontend/src/pages/Monitor.tsx`

- 新增 `const { items: monitorItems } = useMonitorList()`
- `bookmarkSymbols` union `monitorItems.map(i => i.symbol)` 餵 `useWatchlistQuotes`,讓監聽清單股票也有報價
- `BookmarksPanel` 多收 `monitorListItems` prop(用於 sidebar 「監聽」入口的 count)

---

## 8. Error handling

| 場景 | 處理 |
|---|---|
| Discord webhook 未設定 | `discord_notifier.send_signal` 直接 return,no-op |
| Discord webhook 失敗(429 / 網路 / 5xx) | `httpx` 例外捕獲 → `logger.warning` → 不 raise,不影響 ws broadcast + supabase log |
| 監聽清單為空 | `_refill_field_cache` 載到空 set → `field_cache` 空 → `_scope_includes` 永遠 False → 不評估訊號;UI 提示 empty state |
| 監聽清單 add 撞 WS 200 cap | route 先試 `ws_pool.subscribe`,raise `RuntimeError` 就回 503 + `ws_capacity_full`,DB **不寫**(state 一致) |
| 監聽清單 add 後 DB insert 失敗 | rollback ws subscribe,回 409 |
| Migration 重跑 | 所有 `on conflict do nothing` + `if not exists` 確保 idempotent |
| 舊 `scope.symbols` 含 delisted symbol | Backfill 2 用 `exists (select 1 from symbols ...)` 過濾,避免 FK 撞錯 |
| `notify_discord` 對舊 rule 預設 true | 舊 user 未設 webhook → silent skip;設了之後可逐條 toggle off |

---

## 9. Testing

沿用專案 CLAUDE.md Rule 9「測試驗證意圖,不只是行為」。

### 9.1 Unit:`backend/tests/test_signal_engine_monitor.py`(新)

- `_load_monitor_symbols` mock supabase 回固定 list,驗 set 內容 + `user_label` filter 有套
- `_fanout`:
  - `rule.notify_discord=True` + webhook 有設 → `discord_notifier.send_signal` **被呼叫 1 次**
  - `rule.notify_discord=False` → send_signal **不被呼叫**
  - `rule.notify_discord=True` + webhook 未設 → send_signal 進去但 httpx **不被呼叫**
  - Discord raise → `_fanout` 仍正常完成(ws broadcast + supabase append 都跑)
- `_scope_includes`:
  - 監聽清單空時永遠 False
  - 有 symbol 時看 `field_cache` membership

### 9.2 Unit:`backend/tests/test_discord_notifier.py`(新)

- Webhook URL 未設 → no-op,httpx **不被呼叫**
- httpx 5xx / timeout → 不 raise,log warning(用 caplog 驗)
- Embed payload schema(`title` 含 rule_name、`fields` 含 symbol / price / cdp_touch)

### 9.3 Unit:`backend/tests/test_monitor_list_route.py`(新)

- `GET /api/monitor_list` 空 → 200 + `{items: []}`
- `POST /api/monitor_list` 新 symbol → 201 + `ws_pool.subscribe` 呼叫 + `signal_engine.refresh` 呼叫
- `POST` 重複 → 409 + ws rollback(unsubscribe 被呼叫)
- `POST` 不存在 symbol → 404
- `POST` ws_pool.subscribe raise → 503 + DB **無 row**
- `DELETE` → 204 + `ws_pool.unsubscribe` 呼叫

### 9.4 Migration 驗證

```sql
-- backfill 前 + 後 row count
select count(*) from monitor_list;

-- 該 user 的 monitor_list ⊇ 自選書籤 symbols
select symbol from monitor_list where user_label = 'loger'
except
select wi.symbol from watchlist_items wi
  join bookmark_groups bg on bg.id = wi.group_id
  where bg.user_label = 'loger' and bg.name = '自選';
-- 期望:可能多(scope=symbols 來的),不少

-- 跑兩次 migration:row count 不變
```

### 9.5 Frontend(手動驗,不寫 test)

- 監聽清單三個加入入口:① `MonitorListPanel + 新增`、② `IntradayChart + 加入監聽`、③ `AddToBookmarksDialog ☑ 同時加入監聽`
- `ActiveSignalEditor`:新建 rule、編輯舊 rule 都能存(scope 移除後不報錯;`notify_discord` toggle 正確讀寫)

### 9.6 E2E 煙霧(手動,Rule 12 「Fail loud」)

1. 監聽清單加 `2330`
2. 建 rule:`close > 0`(必觸發);`notify_discord` 打開
3. 設定 `SIGNALS_DISCORD_WEBHOOK_URL`,重啟 backend
4. 等盤中第一筆 tick → 確認三件事:
   - 前端觸發歷史列表多一筆
   - `signals_log` 有 row
   - Discord 收到 embed

---

## 10. Open questions / 後續

- 通知格式自訂(per-rule prefix / mention role / colour)— 後續做
- LINE Messaging API — 後續做,需 user 自行申請 channel + bot
- 通知 throttle / batching — 若實務上撞 Discord 429 才回來補
- 監聽清單匯入匯出 / 與書籤批次 sync — 後續看實際使用再決定
