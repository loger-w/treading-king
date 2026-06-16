# 自動監聽選股排程器設計 — 2026-06-16

> **取代**原「大漲股」系統書籤（`top_gainers_scheduler`）。
> 改為自動篩選熱門股 → 訂閱 WS → 納入 signal engine 評估範圍（造山積木 + 未來策略 A/B）。

## 動機

造山積木（PR #33）已在 `signal_engine._update_mountain()` 背景跑，但只有 monitor_list 裡的股票才會被評估。使用者需要一個自動選股機制，把當日符合條件的熱門股納入引擎評估範圍——不靠手動加。

原本的「大漲股」系統書籤只做顯示（WS `owner="system:top_gainers"`），signal engine 不讀。功能重疊度高但定位不同，決定直接取代：刪掉大漲股系統書籤，改為自動監聽排程器。

## 篩選條件

| 條件 | 門檻 | 來源 |
|------|------|------|
| 漲幅 | 3% < changePercent < 9% | server-side `gt=3.0, lt=9.0` |
| 振幅 | `(highPrice - lowPrice) / lowPrice × 100` > 3% | client-side 計算 |
| 成交量 | tradeVolume > 3,000 張 | client-side |
| 股票類型 | COMMONSTOCK（排除 ETF / 特別股） | server-side `type="COMMONSTOCK"` |
| 代碼格式 | 4 位純數字（排除權證 / 可轉債） | client-side regex |
| 本機快取 | 須在 symbols 快取中 | client-side（快取未載入時跳過此篩） |

**上限**：100 檔。超過後不再加新股票。

**漲幅上限 9%** 排除接近漲停的股票——鎖死期間沒有成交、tick 不流動，監聽沒意義。

## 更新策略：滾動只加不減

- 每 1 分鐘掃描一次（盤中 09:00–13:30）
- 新符合條件的加入（WS subscribe + signal engine 納入）
- **已加入的當天不移除**——造山狀態機跑到一半不能中斷
- 收盤後一次性全清（WS unsubscribe + signal engine 逐出）

## 生命週期

| 時段 | 行為 |
|------|------|
| 09:00–13:30 | 每分鐘掃描 `snapshot.movers`，新股加入 auto set |
| 08:30–09:00 | 不跑（避免試撮雜訊） |
| 13:30 後第一輪 | 退訂所有 auto_monitor WS + 清 signal engine auto set |
| 隔天 09:00 | 重新從零開始掃描 |
| 程式重啟 | auto set 歸零（純記憶體），盤中重啟自動重建 |

## 架構

### 後端

```
auto_monitor_scheduler.py（改造自 top_gainers_scheduler.py）
  │
  │  snapshot.movers(TSE, up, gt=3.0, lt=9.0)
  │  snapshot.movers(OTC, up, gt=3.0, lt=9.0)
  │  → client-side 篩：amp > 3%, vol > 3000, 4位數, 在 symbols 快取
  │  → 排序 changePercent desc → cap 100
  │
  ├─ 新股 diff（not in _auto_set）
  │    → ws_pool.subscribe(owner="auto_monitor")
  │    → signal_engine.add_auto_symbols(new_symbols)
  │         → _refill_auto_field_cache(new_symbols)  # 增量載入 CDP + SMA
  │    → 更新 market_cache.auto_monitor snapshot（供前端 API）
  │
  └─ 收盤
       → ws_pool.unsubscribe all auto_monitor
       → signal_engine.clear_auto_symbols()
       → 清 market_cache.auto_monitor snapshot
```

### Signal Engine 改動

新增記憶體暫態：

```python
_auto_monitor_symbols: set[str] = set()
```

新增方法：

```python
async def add_auto_symbols(self, symbols: set[str]) -> None:
    """增量加入 auto_monitor 股票。只對新股票載入 field_cache。"""
    new = symbols - self._auto_monitor_symbols
    if not new:
        return
    self._auto_monitor_symbols |= new
    await self._refill_auto_field_cache(new)

async def clear_auto_symbols(self) -> None:
    """收盤清理：清 auto set + 逐出 field_cache 中 auto-only 的 entry。"""
    # 只逐出不在 config.monitor_list 裡的（避免誤刪手動監聽的）
    manual = await self._load_config_monitor_symbols()
    auto_only = self._auto_monitor_symbols - manual
    for sym in auto_only:
        self._field_cache.pop(sym, None)
    self._auto_monitor_symbols.clear()

async def _refill_auto_field_cache(self, symbols: set[str]) -> None:
    """增量載入 CDP + SMA。跟 _refill_field_cache 共用底層邏輯。"""
    for sym in symbols:
        if sym not in self._field_cache:
            self._field_cache[sym] = {}
        await self._load_cdp_sma_for_symbol(sym)
```

修改 `_load_monitor_symbols()`：

```python
async def _load_monitor_symbols(self) -> set[str]:
    return self._load_config_monitor_symbols() | self._auto_monitor_symbols

async def _load_config_monitor_symbols(self) -> set[str]:
    """原本的 _load_monitor_symbols 邏輯：只讀 config。"""
    return {m["symbol"] for m in get_local_store().config.list_monitor()}
```

### Market Cache 改動

`market_cache.py` 將 `_top_gainers` 改名為 `_auto_monitor`，方法同理：

```python
_auto_monitor: list[dict] = []

def get_auto_monitor(self) -> list[dict]: ...
def replace_auto_monitor(self, rows: list[dict]) -> None: ...
def auto_monitor_count(self) -> int: ...
```

### API 端點

新增 `routes/auto_monitor.py`：

```
GET /api/auto_monitor
  → { items: [...], count: N }
  → 每個 item: { symbol, name, change_pct, amplitude_pct, volume_lots, market, rank, added_at }
  → 用 enrich_item 補 name/market metadata
```

刪除：
- `POST /api/bookmarks/top-gainers/refresh`（大漲股手動 refresh 端點）
- 書籤 route 中 `SYSTEM_TOP_GAINERS_*` 系統書籤合成邏輯

### 前端

#### 刪除

- 書籤中「大漲股」系統書籤相關邏輯（`is_system`、`source_type: "top_gainers"`）
- `BookmarkGroup.source_type` 欄位（只有大漲股用到）
- `BookmarkItem` 的 `change_pct`/`volume_lots` 欄位（大漲股專用）
- `quote-display.test.ts` 中大漲股 fallback 測試

#### 新增

Sidebar 新增 `auto_monitor` 頁面入口：

```typescript
// Sidebar.tsx
export type Page = 'monitor' | 'auto_monitor' | 'mxf_backtest' | 'index_board';

const NAV_ITEMS: NavItem[] = [
  { id: 'monitor', label: '即時監控', iconPath: '...' },
  { id: 'auto_monitor', label: '自動監聽', iconPath: '...' },  // 新增
  { id: 'mxf_backtest', label: '小台指策略回測 (MXF)', iconPath: '...' },
  { id: 'index_board', label: '大盤指數', iconPath: '...' },
];
```

新增 `pages/AutoMonitor.tsx`：

- `GET /api/auto_monitor` 取清單
- 輪詢或 WS 更新（auto_monitor 的股票已訂閱 WS，複用 `useTickStore`）
- 顯示欄位：代號、名稱、現價、漲跌%、振幅%、成交量、加入時間
- 唯讀（排程器全權控制，使用者不能手動加刪）
- 空態提示：「盤中自動篩選中…」（非盤中）或「尚無符合條件的股票」

## WS 預算分析

| 用途 | 佔用 | 說明 |
|------|------|------|
| auto_monitor | 最多 100 | cap 100、只加不減 |
| monitor_list（手動） | 使用者自訂 | 通常 5-20 |
| 書籤群組 | 依書籤數 | 複用 refcount 去重 |
| preview | 1 | 當前圖表 |
| **去重後合計** | ~100-130 | 重疊的只佔 1 格 |

200 格 WS 上限下，100 檔 auto + 手動監聽 + 書籤 + preview 安全落在 ~130 格內。

**刪掉 top_gainers 釋放 ~50 格**——原本 top_gainers 佔最多 50 格，現在改為 auto_monitor 佔最多 100 格，淨增 ~50 格但價值更高（signal engine 評估 vs 純顯示）。

## REST Rate Limit 影響

- `snapshot.movers` 每分鐘 2 次（TSE + OTC）→ 不變（本來就打）
- 新股 CDP + SMA 增量載入：每新股 ~2 calls（5 req/s 限速下）
  - 最差情況（開盤第一分鐘一次加 50 檔）：100 calls / 5 = 20 秒
  - 正常情況（每分鐘新增 0-5 檔）：<2 秒
- 不影響 `historical` rate limiter（1 req/s），因為 CDP/SMA 走 `intraday` 帳本

## 刪除清單（大漲股相關）

### 後端

| 檔案 | 刪除內容 |
|------|---------|
| `jobs/top_gainers_scheduler.py` | 整個改造（改名為 `auto_monitor_scheduler.py`） |
| `routes/bookmarks.py` | `SYSTEM_TOP_GAINERS_*` 常數、系統書籤合成邏輯、`/api/bookmarks/top-gainers/refresh` 端點 |
| `services/local_store/market_cache.py` | `_top_gainers` / `get_top_gainers` / `replace_top_gainers` / `top_gainers_count` → 改名 |
| `main.py` | `from jobs.top_gainers_scheduler` → 改為 `auto_monitor_scheduler` |
| `tests/test_bookmarks_route.py` | top_gainers 相關測試 |
| `tests/test_market_cache.py` | top_gainers 相關測試 |

### 前端

| 檔案 | 刪除內容 |
|------|---------|
| `lib/api.ts` | `BookmarkGroup.source_type`、`BookmarkItem.change_pct`/`volume_lots` |
| `lib/quote-display.test.ts` | 大漲股 fallback 測試 |
| `components/BookmarksPanel.tsx` | `is_system` 分區渲染邏輯 |
| `hooks/useBookmarks.ts` | 系統書籤相關邏輯（如有） |

## 不動的

- `signal_engine._update_mountain()`：照舊每根 settled candle 跑
- `signal_engine._mountain_state`：照舊 daily reset 清
- config.json `monitor_list`：手動監聽不受影響
- 既有策略（cdp_proximity / breakout_confirm / 突爆殺等）：auto_monitor 股票一樣適用

## 測試策略

| 層級 | 測試項目 |
|------|---------|
| 單元 | `auto_monitor_scheduler`：篩選邏輯（amplitude 計算、邊界值、cap 100） |
| 單元 | `signal_engine`：`add_auto_symbols` / `clear_auto_symbols` / `_load_monitor_symbols` 合併行為 |
| 單元 | `market_cache`：`replace_auto_monitor` / `get_auto_monitor` |
| 整合 | `routes/auto_monitor`：GET 回正確結構 |
| 手動 | 盤中觀察：自動加入數量、WS 佔用、造山 state 有無跑起來 |
