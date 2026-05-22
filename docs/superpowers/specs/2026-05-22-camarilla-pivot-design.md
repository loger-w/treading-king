# Camarilla Pivot 八線 — 主圖 horizontal level

**Date**: 2026-05-22
**Status**: Implemented(commits `eea71d4`..`c68495b`)
**Branch**: `feat/intraday-indicators`(從 dd90d8c 重建,Camarilla 在其上實作)

## Summary

加入 Camarilla Pivot 八線指標,在主圖跟 CDP 並列。從昨日 OHLC 算出 H1–H4 / L1–L4 共 8 條
horizontal level,提供「H3/L3 反轉、H4/L4 突破」雙策略。

跟 CDP 形成寬窄雙視角:CDP 5 線通常落在 ±2% 內(對台股日內波幅恰好),Camarilla H4/L4
通常落在 ±5%–±10% 區間,捕捉較大波段。

## Goals

- 後端從昨日 OHLC 算出 8 條 Camarilla level,複用 CDP 的 `daily_ohlc` 流程與台股 tick rounding
- 提供 `GET /api/camarilla/{symbol}` endpoint,回 8 條 level + `as_of_date` + `prev_close`
- 主圖加 `CAM` toggle 按鈕(預設關閉)、藍 dotted 8 條 line、4 條主 label
- 沿用既有 ±10% Y 軸可見性過濾與 right-margin label 碰撞撐開機制

## Non-goals

- **不做** Camarilla 自動訊號偵測 / 進入 `active_signals` — 純視覺指標,觸發邏輯之後另開 spec
- **不抽** daily-OHLC fetch helper(CDP 跟 Camarilla code duplication 暫時保留,加第三個 indicator 才重構)
- **不存** Camarilla level 進 DB — in-memory cache 即可(每日重算、不需歷史紀錄)
- **不改** CDP 既有結構

## Architecture

```
backend/
├── services/
│   ├── camarilla.py         [新] 純函式 + CamarillaService,鏡像 services/cdp.py
│   └── cdp.py               [不動]
└── routes/
    ├── camarilla.py         [新] GET /api/camarilla/{symbol}
    └── cdp.py               [不動]

frontend/src/
├── lib/
│   └── api.ts               [改] +api.camarilla() + CamarillaLevels type
└── components/
    └── IntradayChart.tsx    [改] +showCamarilla state + 抓取 + 渲染 + toggle
```

**Service 結構** — `CamarillaService` 鏡像 `CdpService`,各自 in-memory cache、各自 backfill
attempt 紀錄,讀寫 `daily_ohlc` 表的邏輯重複(可接受):

```python
class CamarillaService:
    _cache: dict[str, CamarillaLevels]
    _last_backfill_attempt: dict[str, date]

    async def get(symbol) -> CamarillaLevels | None
    async def refresh(symbol) -> None
    async def backfill_from_fubon(symbol) -> bool
    def discard(symbol) -> None
    def has(symbol) -> bool
```

`backfill_from_fubon` 跟 CDP 用同一個 `get_historical_rate_limiter()`(60 req/min)、同一個
`fubon.sdk.marketdata.rest_client.stock.historical.candles` 呼叫、upsert `daily_ohlc` 同邏輯。
**Trade-off**:code duplication 跟 CDP 各 ~30 行。先接受,加第三個 daily-OHLC-based indicator
時再抽 helper。

`compute_camarilla()` 是純函式、export 出來、單獨可測:

```python
def compute_camarilla(h: float, l: float, c: float) -> dict[str, float]:
    rng = h - l
    raw = {
        "h4": c + rng * 1.1 / 2,
        "h3": c + rng * 1.1 / 4,
        "h2": c + rng * 1.1 / 6,
        "h1": c + rng * 1.1 / 12,
        "l1": c - rng * 1.1 / 12,
        "l2": c - rng * 1.1 / 6,
        "l3": c - rng * 1.1 / 4,
        "l4": c - rng * 1.1 / 2,
    }
    # 全部 round_to_tick_tw(_, "nearest") 對齊台股 tick
    return {k: round_to_tick_tw(v, "nearest") for k, v in raw.items()}
```

注意 — `round_to_tick_tw` 是 `services/cdp.py:40` 的 helper。**直接 import 使用,不複製**
(這個 helper 是 OHLC 處理的通用工具、不是 CDP 專屬)。

## API Contract

```http
GET /api/camarilla/{symbol}
→ 200
{
  "h4": 605.00, "h3": 595.00, "h2": 591.50, "h1": 588.00,
  "l1": 581.50, "l2": 578.00, "l3": 574.50, "l4": 564.50,
  "as_of_date": "2026-05-21",
  "prev_close": 584.50
}
→ 503 { "error": "camarilla_data_unavailable", "symbol": "2330" }
→ 503 { "error": "camarilla_data_unavailable_after_backfill" }
```

Route handler 鏡像 `routes/cdp.py`:

```python
@router.get("/api/camarilla/{symbol}")
async def get_camarilla(symbol: str) -> dict:
    levels = await get_camarilla_service().get(symbol)
    if levels is None:
        ok = await get_camarilla_service().backfill_from_fubon(symbol)
        if not ok:
            raise HTTPException(503, detail={"error": "camarilla_data_unavailable", "symbol": symbol})
        levels = await get_camarilla_service().get(symbol)
        if levels is None:
            raise HTTPException(503, detail={"error": "camarilla_data_unavailable_after_backfill"})
    return levels
```

Router 註冊 — 在 `backend/main.py` `include_router(camarilla.router)` 跟 cdp 並列。

## Frontend UI

**State + 抓取**(在 `IntradayChart.tsx` 既有 CDP pattern 旁邊加):

```tsx
const [showCamarilla, setShowCamarilla] = useLocalToggle("tk:chart:camarilla", false);
const [camarilla, setCamarilla] = useState<CamarillaLevels | null>(null);
const [camarillaError, setCamarillaError] = useState<string | null>(null);

useEffect(() => {
  setCamarilla(null);
  setCamarillaError(null);
  if (!showCamarilla) return;
  api.camarilla(symbol).then(setCamarilla).catch((e) =>
    setCamarillaError(e instanceof Error ? e.message : String(e))
  );
}, [symbol, showCamarilla]);
```

**Y 軸過濾**(沿用 CDP 同邏輯):

```tsx
const allCamKeys = ["h4","h3","h2","h1","l1","l2","l3","l4"] as const;
const visibleCamKeys = (showCamarilla && camarilla)
  ? allCamKeys.filter((k) => camarilla[k] >= refMin && camarilla[k] <= refMax)
  : [];
```

**繪製樣式**:

| Key       | Stroke width | Opacity | Dashed       | Right-margin label |
|-----------|--------------|---------|--------------|--------------------|
| H4 / L4   | 0.8          | 0.75    | `2 4` dotted | ✓                  |
| H3 / L3   | 0.8          | 0.75    | `2 4` dotted | ✓                  |
| H2 / L2   | 0.6          | 0.40    | `2 4` dotted | ✗                  |
| H1 / L1   | 0.6          | 0.40    | `2 4` dotted | ✗                  |

顏色 `#3b82f6`(Tailwind blue-500),跟 CDP 紅 / MA5 黃 / MA20 紫 / VWAP 灰 區隔。
Label 只顯示 4 條主 level(H3/L3/H4/L4),避免跟 CDP 5 個 + MA 2 個 + VWAP 1 個 共 12 個 label
擠在右邊 80px。Hover 時 弱 line(H1/H2/L1/L2)的數值由 crosshair tooltip 顯示
(後續可實作、本 spec 不含)。

**Toggle**:在 toolbar 現有 `VWAP / CDP / VOL / MA / ELDER / FISHER / STC` 序列中加 `CAM`,放在
`CDP` 後面、`VOL` 前面(同類 horizontal level 群組)。預設 off。

## Data 流

```
富邦 historical.candles ──┐
                          ├─→ daily_ohlc 表(共用) ──┐
                          ┘                           ├─→ CdpService.cache    ──→ /api/cdp/{symbol}
                                                      └─→ CamarillaService.cache ──→ /api/camarilla/{symbol}
                                                                                       ↓
                                                                            IntradayChart 主圖 8 線
```

`daily_ohlc` 表已存在,row schema `(symbol, date, high, low, close)`。CDP 跟 Camarilla 都讀同樣的
最近一筆 row,各自轉成自己的 level 結構放 cache。

Lazy backfill:每天首次呼叫某 symbol 的 endpoint 會 trigger 一次 `backfill_from_fubon`,把昨日
OHLC 寫進 daily_ohlc。CDP 跟 Camarilla 各自跑一次(冗餘但便宜)— Historical limiter 會排隊。

## 錯誤處理

- **富邦未連線** → 503 `camarilla_data_unavailable` (回 fallback 既有 daily_ohlc 若有的話)
- **Supabase 未連線** → 503,前端顯示「Camarilla 無資料: …」(沿用 CDP error UI)
- **昨日為非交易日 / 資料缺漏** → 抓 10 天範圍, fubon 回 desc, 過濾掉今日 → 取最近一筆
- **OHLC 含 NaN / 負值** → ValueError, log warning, 不寫 cache → 前端顯示 error

## 測試

**後端** `backend/tests/test_camarilla.py`:

- `test_compute_camarilla_reference_values` — 對 `compute_camarilla(110, 90, 100)` 做 known-value
  比對(`rng=20`,`h4 = 100 + 11 = 111`,`l4 = 89`,etc.)
- `test_compute_camarilla_tick_rounding` — 中價 50.5/49.5/50.0 確認 0.05 tick 對齊
- `test_compute_camarilla_high_price_tick` — 1000+ 價位確認 5.0 tick 對齊
- `test_service_refresh_caches_levels` — mock `daily_ohlc` 回 row → cache 命中
- `test_service_get_triggers_daily_backfill` — 模擬「首日呼叫」trigger backfill 一次,第二次不再 trigger

**前端** — 既有 indicators.test.ts 是純前端指標,Camarilla 是後端;不新增測試,靠手動驗收
(實際打 API + 視覺檢查 8 線分佈合理)。

## 後續路線(B、C 排在後)

完成 Camarilla 後依序:

1. **B. Choppiness Index** — 副圖 oscillator,純前端 `computeChoppiness` 加進 `lib/indicators.ts`,
   IndPane 跟 STC 同模式。0-100、>61 紅背景(盤整)、<38 綠背景(強趨勢)。獨立 spec
2. **C. Volume Profile + POC / VAH / VAL** — 主圖右側橫向 histogram + 三條線。需要新的 SVG
   layout 跟 price-bin allocation 演算法。獨立 spec、實作較重

兩者都不需後端 / DB / 富邦 API,純前端從既有 candles 計算。

## 風險與已知限制

- **8 條線視覺密度** — 加上 CDP 5 條 + MA 2 條 + VWAP 1 條 共 16 條 horizontal,即使 Camarilla
  弱化 H1/H2/L1/L2,長時間開所有 toggle 會 clutter。Mitigation:user 應該按需 toggle,不要全開
- **Camarilla 1.1 倍係數來源** — 公式 `rng × 1.1 / N` 的 1.1 是 Nick Stott 原始設計,**沒有明確
  數學推導**,純經驗常數。算當代 quant 不夠 rigor,但歷史 PnL 在 forex / equities / 印度
  intraday 圈有 anecdotal 支持
- **昨日為長假期(過年)** — 跳過 10+ 天再算的 Camarilla 已經跟今日連續性弱,訊號品質下降
  (CDP 有同樣問題,共用 mitigation:user 自行判斷)

## References

- Nick Stott / Slim Pivots,Camarilla Equation,1990s
- ChartSchool / Babypips / LizardIndicators 介紹文(見 brainstorming session WebSearch 結果)
