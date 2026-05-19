# Chart Polish + Signal Enrichment

**Date**: 2026-05-19
**Status**: Brainstorming(待 writing-plans 接手)

## Summary

一輪 bundled 改動:即時走勢圖 label 視覺精修、五檔鎖漲跌停的市價買賣顯示修復、訊號規則新增 MA 觸發、以及 CDP 觸發訊息加上方向(支撐/壓力)與當天觸碰次數。共 4 個 PR。

## Goals

- 即時走勢圖右邊 margin 不再因為同時開 CDP + MA + VWAP 而 label 重疊
- 鎖漲停 / 跌停的股票在五檔顯示「市價買 / 市價賣」,不再出現「0.00」
- 訊號規則可以用 MA5 / MA20 當條件(跨欄位比較 + proximity 觸發)
- CDP 觸發 fanout 帶方向(由下往上=壓力 / 由上往下=支撐)與當天該線的第幾次觸碰

## Non-goals

- **墊單偵測(內外盤大單塞入)** — 工程量需另開 spec(要訂閱 fubon books channel、新增 book buffer、新 condition type)
- 不重寫 CDP / MA 既有 visual 風格(僅補資訊,不換配色)
- 不引入第三方圖表庫
- 不調整 watchlist / signal log DB schema(filter_json JSONB schema_version bump 就好)

## Architecture

```
frontend/src/
├── components/
│   ├── IntradayChart.tsx               [改] MA label 文字移除 + label 碰撞撐開
│   ├── QuoteBook.tsx                   [改] price=0 顯示「市價」
│   ├── ActiveSignalEditor.tsx          [改] MA cross-field + MA proximity UI
│   ├── TriggerList.tsx                 [改] 顯示方向 / 觸碰次數
│   └── SignalChip.tsx                  [改] 顯示方向 / 觸碰次數
├── lib/
│   ├── api.ts                          [改] QuoteResponse 加 isLimitUp/Down flags
│   │                                        ConditionField 加 sma_5/sma_20
│   │                                        ActiveFilter 加 ma_proximity
│   └── chart-labels.ts                 [新] resolveCollisions 純函式 + 單元測試

backend/
├── routes/
│   └── quote.py                        [改] forward isLimitUp/Down flags
├── models/
│   └── condition.py                    [改] sma_5/sma_20 進 ConditionField
│                                            新增 MAProximityCondition
│                                            ActiveFilter schema 2→3 (2 = cdp_proximity,3 = + ma_proximity)
├── services/
│   ├── ma_service.py                   [新] _fetch_sma 從 routes/ma.py 抽出來
│   ├── signal_engine.py                [改] _refill_field_cache 加 MA
│   │                                        _eval_cdp_proximity 回 (bool, level)
│   │                                        新增 _eval_ma_proximity
│   │                                        新增 _cdp_touch_count + 跨日 GC
│   │                                        fanout payload 加 cdp_touch / ma_touch
│   └── ring_buffer.py                  [不動]
└── routes/
    └── ma.py                           [改] 改用 services/ma_service.py
```

---

## Item 1 · 移除 MA5 / MA20 文字標籤

**問題**:`IntradayChart.tsx:315` 目前 label 是「MA5 123.50」「MA20 123.20」。MA5 黃 / MA20 紫 已經建立色彩辨識,文字 prefix 多餘。

**改動**:單行。

```tsx
// before
{isShort ? "MA5" : "MA20"} {formatTickPrice(v)}
// after
{formatTickPrice(v)}
```

label 變短後也讓 Item 2 的撐開演算法有更多 padding 空間。

---

## Item 2 · 右邊 label 碰撞自動撐開

**問題**:同時開 CDP + MA5 + MA20 + VWAP 時,右邊 margin 同 y 範圍可能塞 5~8 個 label,互相蓋住看不清。

**演算法**(新 helper `frontend/src/lib/chart-labels.ts`,純函式):

```ts
export interface LabelInput {
  originalY: number;
  text: string;
  color: string;
  align?: "right" | "left";  // VWAP 在線端、其他在 margin
}

export interface LabelOutput extends LabelInput {
  y: number;  // 碰撞解決後的位置
}

export function resolveCollisions(
  items: LabelInput[],
  minGap: number,
  yRange: [number, number],
): LabelOutput[]
```

**Pass 1**(往下推)依 originalY 升序排,從上往下掃,`y[i] = max(y[i], y[i-1] + minGap)`。

**Pass 2**(回彈)如果 `y[last] > yRange[1]`,從下往上掃,`y[i] = min(y[i], y[i+1] - minGap)`,把超出下界的整組向上推。

**選 `minGap = 16`**(label 高度 12px + 4px 透氣)。

**Render** — 在 `IntradayChart.tsx` useMemo 內把 CDP / MA / VWAP label 統一收集進 `LabelInput[]`,呼叫 `resolveCollisions`,render 時:

```tsx
// 引導線:y !== originalY 時畫一條短線從原 y 拉到撐開後的 y
{y !== originalY && (
  <line x1={CHART_W - PAD_R} y1={originalY}
        x2={CHART_W - PAD_R + 4} y2={y}
        stroke={color} strokeWidth="0.7" opacity="0.5" />
)}
<text x={CHART_W - PAD_R + 6} y={y + 3} fill={color}>{text}</text>
```

**範圍**:涵蓋右邊 margin 的 CDP 5 線 + MA5 + MA20 + VWAP last value。**不涵蓋**:hover crosshair 的左邊價位 chip、今日 high/low(黏在 candle 上不在 margin)、成交量子圖 label。

---

## Item 3 · 鎖漲跌停顯示市價買 / 市價賣

**問題**:富邦 `intraday/quote` 在股票鎖漲停時,委賣側會回 `price=0` 表示「市價買單在排隊吃」(對手檔不存在);跌停時委買側 `price=0` 表 「市價賣單在排隊吃」。後端 `routes/quote.py:40-43` 只 forward `bids` / `asks`,把 `isLimitUpBid` / `isLimitDownAsk` 等 flag 丟掉,前端 `QuoteBook.tsx:58,73` 直接 `price.toFixed(2)` 印成「0.00」。

**Backend** `routes/quote.py`:

```python
return {
    "bids": result.get("bids", []),
    "asks": result.get("asks", []),
    "is_limit_up_bid":   result.get("isLimitUpBid", False),
    "is_limit_up_ask":   result.get("isLimitUpAsk", False),
    "is_limit_down_bid": result.get("isLimitDownBid", False),
    "is_limit_down_ask": result.get("isLimitDownAsk", False),
}
```

`api.ts` `QuoteResponse` 對應加 4 個 optional boolean。

**Frontend** `QuoteBook.tsx` — 委買 5 檔 row 內 `price === 0` 時:
- 顯示「市價」紅色文字代替數字(bull 色,跟既有買盤同調)
- size 仍然顯示張數(可能是 0、可能是有量,看富邦回什麼)

委賣同理顯示「市價」(bear 綠色)。

決定哪邊是市價的判定:
- `is_limit_up_bid && bid.price === 0` → 不會發生(漲停鎖死時委買有價、委賣才會 price=0)
- 實務上判 `price === 0` 已足夠;flag 只用來顯示一個小 badge「鎖漲停」/「鎖跌停」放在五檔 header,讓 user 一眼看出狀態

**QuoteBook header** 多一個 badge:

```tsx
{(isLimitUpBid || isLimitUpAsk) && <Badge color="bull">鎖漲停</Badge>}
{(isLimitDownBid || isLimitDownAsk) && <Badge color="bear">鎖跌停</Badge>}
```

---

## Item 4a · MA cross-field condition

**問題**:目前 `ConditionField` 只有 `close` + 5 條 CDP,無法表達「即時價 ≥ MA20」(站上 20 日線)。

**改動**:

`backend/models/condition.py`:

```python
ConditionField = Literal[
    "close",
    "cdp_ah", "cdp_nh", "cdp", "cdp_nl", "cdp_al",
    "sma_5", "sma_20",  # NEW
]
ALL_FIELDS = (
    "close",
    "cdp_ah", "cdp_nh", "cdp", "cdp_nl", "cdp_al",
    "sma_5", "sma_20",
)
```

`backend/services/ma_service.py`(新)— 從 `routes/ma.py` 把 `_fetch_sma` / `_extract_latest` 抽出來:

```python
async def fetch_sma(symbol: str, period: int) -> float | None
async def fetch_sma_5_20(symbol: str) -> tuple[float | None, float | None]
```

`routes/ma.py` 改用 `ma_service`,signal_engine 也用同一個。

`signal_engine._refill_field_cache` 在 CDP refill 之後加:

```python
for sym in symbols_needed:
    sma_5, sma_20 = await ma_service.fetch_sma_5_20(sym)
    d = self._field_cache.setdefault(sym, {})
    if sma_5  is not None: d["sma_5"]  = sma_5
    if sma_20 is not None: d["sma_20"] = sma_20
```

跨午夜 heartbeat 自動 refresh(現成機制不動)。

**Frontend** `ActiveSignalEditor.tsx` `FIELD_LABEL`:

```tsx
const FIELD_LABEL = {
  close: "即時價",
  cdp_ah: "CDP AH …", /* 5 條 CDP */
  sma_5: "MA5", sma_20: "MA20",  // NEW
};
```

`api.ts` `ALL_FIELDS` 同步加。

---

## Item 4b · MA proximity 觸發

**問題**:user 想要「打到 5 日線」式的 proximity 觸發,跟 CDP 那個「碰到 5 線之一」對稱。

**新 model**:

```python
class MAProximityCondition(BaseModel):
    levels: list[Literal["sma_5", "sma_20"]] = Field(
        default_factory=lambda: ["sma_5", "sma_20"], min_length=1)
    tolerance_ticks: int = Field(default=0, ge=0, le=10)
```

`ActiveFilter`:

```python
class ActiveFilter(Filter):
    schema_version: int = 3  # 2→3,加 ma_proximity
    window_conditions: list[WindowCondition] = Field(default_factory=list)
    cdp_proximity: CdpProximityCondition | None = None
    ma_proximity:  MAProximityCondition  | None = None  # NEW

    @model_validator(mode="after")
    def conditions_non_empty(self):
        if (not self.conditions
                and not self.window_conditions
                and self.cdp_proximity is None
                and self.ma_proximity is None):
            raise ValueError(...)
        return self
```

**Backwards compat**:DB 內現有 filter_json 是 schema 1 或 2,Pydantic 預設值會自動補 `ma_proximity=None`,可正常 load。

**signal_engine 新增** `_eval_ma_proximity`(跟 `_eval_cdp_proximity` 同結構):

```python
def _eval_ma_proximity(self, symbol, tick, prox) -> tuple[bool, str | None]:
    """tick.price 落在所選 MA 線的 ±N tick 範圍內 → (True, 哪條觸發)。"""
    from services.cdp import tick_size
    cache = self._field_cache.get(symbol, {})
    levels = prox.get("levels") if isinstance(prox, dict) else prox.levels
    tol_ticks = ...
    for level in levels:  # "sma_5" or "sma_20"
        v = cache.get(level)
        if v is None: continue
        tol = tol_ticks * tick_size(v)
        if abs(tick.price - v) <= tol:
            return True, level
    return False, None
```

注意:`cache[level]` 是 raw 算術平均,常落在非合法 tick;tolerance=0 時用 `abs(tick.price - v) == 0` 會永遠 false。實務上 user 想要 tolerance ≥ 1 才合理 — UI 預設 1。

`_eval_conditions` 把 ma_proximity 結果納入 AND/OR 邏輯。

**Frontend** `ActiveSignalEditor.tsx` 加一個「MA 觸發」區塊,UI 跟「CDP 觸發」對稱:

```tsx
<div className="border-t border-line pt-3 mb-4">
  <div className="label-tiny mb-2">MA 觸發</div>
  <p className="text-2xs text-ink-dim mb-3">
    價格打到(或接近)所選 MA 線即觸發。Tolerance 建議 ≥ 1 tick(SMA 不在合法 tick 上)。
  </p>
  {!filter.ma_proximity
    ? <Button onClick={enableMaProx}>+ 啟用 MA 觸發</Button>
    : <MaProxEditor ... />}
</div>
```

---

## Q1 · CDP 觸發方向(支撐 / 壓力)

**問題**:目前 CDP 觸發只有「打到」,沒區分由下往上(壓力)還是由上往下(支撐)。

**改動** `signal_engine._evaluate`:

```python
async def _evaluate(self, symbol, tick):
    for active in self._active:
        if not self._scope_includes(active, symbol): continue
        ok, cdp_level, ma_level = self._eval_conditions(active, symbol, tick)
        if not ok: continue

        # cooldown 檢查 ...

        # 方向判斷(若是 CDP proximity 觸發)
        cdp_touch = None
        if cdp_level:
            prev = self._prev_tick.get(symbol)
            v = self._field_cache[symbol][f"cdp_{cdp_level}" if cdp_level != "cdp" else "cdp"]
            direction = self._direction_of_touch(prev, tick, v)
            role = {"from_below": "resistance", "from_above": "support"}.get(direction, "touch")
            cdp_touch = {"level": cdp_level, "direction": direction, "role": role}

        # ... fanout 帶 cdp_touch
        self._prev_tick[symbol] = tick
```

`_direction_of_touch(prev, curr, threshold)`:

```python
def _direction_of_touch(prev, curr, v):
    if prev is None: return "horizontal"
    if prev.price < v and curr.price >= v: return "from_below"
    if prev.price > v and curr.price <= v: return "from_above"
    return "horizontal"
```

`_prev_tick: dict[str, Tick]` 每次 evaluate 完更新一次(用 ring_buffer 倒數第二筆也行,但這樣比較直接)。

**MA proximity 同理** — `ma_touch: { level, direction, role }`,role 用同樣的「壓力 / 支撐」邏輯。

**前端** `TriggerList.tsx` 顯示訊息把 role 翻成中文:

| direction | role | 顯示 |
|---|---|---|
| from_below | resistance | 「碰到壓力」 |
| from_above | support | 「碰到支撐」 |
| horizontal | touch | 「平觸」 |

---

## Q2 · 同 CDP 線當天第幾次觸碰

**問題**:user 想看到「第 3 次碰到 CDP AH」這種計數。

**改動** `signal_engine`:

```python
self._cdp_touch_count: dict[tuple[str, str, date], int] = {}
self._ma_touch_count:  dict[tuple[str, str, date], int] = {}
```

`_evaluate` 在 cooldown 通過後 +1:

```python
if cdp_level:
    key = (symbol, cdp_level, date.today())
    self._cdp_touch_count[key] = self._cdp_touch_count.get(key, 0) + 1
    cdp_touch["touch_index"] = self._cdp_touch_count[key]
```

**跨日 GC** — `_heartbeat_loop` 在跨午夜 refill 時順便清舊 date key:

```python
today = date.today()
if self._last_field_refill_date != today:
    await self._refill_field_cache()
    self._cdp_touch_count = {k: v for k, v in self._cdp_touch_count.items() if k[2] == today}
    self._ma_touch_count  = {k: v for k, v in self._ma_touch_count.items()  if k[2] == today}
```

(實作上前一日的 key GC 掉就好,當天的留著繼續累計。)

**訊號訊息範例**:
- 「2330 第 3 次碰到壓力 · CDP AH @ 658.00」
- 「2454 第 1 次平觸 · MA20 @ 1245.00」

---

## Testing strategy

| Item | 測試 |
|---|---|
| 1 | 視覺驗 — chart 切 MA toggle 看是否只剩數字 |
| 2 | Vitest unit test `resolveCollisions` — 無重疊、兩個重疊、5 個全擠、推出邊界要回彈 |
| 3 | 找鎖漲停股(收盤前查 TWSE volatile list)或 mock 富邦回 `price=0 + isLimitUpAsk=true` |
| 4a | Pytest signal_engine — 設 `close ≥ sma_5`,推 tick,assert 觸發;sma_5=None 時不 crash |
| 4b | Pytest — `ma_proximity levels=["sma_5"]` tolerance=1,推 price=sma_5±tick_size 的 tick,assert 觸發 |
| Q1 | Pytest — 連推兩筆 tick `[122, 124]` 過 CDP=123,assert direction=`from_below` / role=`resistance` |
| Q2 | Pytest — 同 symbol 同 level 連觸發 3 次(各次 ≥ cooldown),assert touch_index=1,2,3 |

---

## PR breakdown

1. **`feat(frontend): chart label polish`** — Item 1 + Item 2
2. **`fix(quotebook): show 市價 on limit-up/down lock`** — Item 3(backend + frontend)
3. **`feat(signals): MA conditions + proximity`** — Item 4a + 4b(model + engine + UI)
4. **`feat(signals): CDP/MA direction + touch count`** — Q1 + Q2

PR 1 / 2 互不影響可以同時開,PR 3 / 4 依序(PR 4 改 signal_engine 的範圍跟 PR 3 重疊,要排隊)。

---

## Open questions / risks

- **`_prev_tick` 跟 heartbeat**:heartbeat 用 ring_buffer 最後一筆重評估時,prev_tick 會是「同一筆」造成 direction=horizontal。實作時 evaluate 內如果 tick 跟 `_prev_tick[symbol]` 是同 instance 就跳過 _prev_tick 更新,確保只有 tick-driven 路徑算方向。
- **MA proximity tolerance**:UI 預設應該是 1 不是 0(SMA raw 不在合法 tick),寫進 `MAProximityCondition` 的 default 或 ActiveSignalEditor 預填都行,後者比較好(只影響新建,不動既有資料)。
- **Schema bump 2→3**:Pydantic 預設值能讓 schema 2 的舊 filter_json 自動補 `ma_proximity=None`,但要寫個 unit test 確認 load 不會 crash。
