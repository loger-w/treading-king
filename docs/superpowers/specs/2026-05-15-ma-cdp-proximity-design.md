# Monitor 擴充:日 MA 線 + CDP 觸發訊號條件

## 背景

使用者觀察到兩個操作上的缺口:

1. 即時走勢圖目前只有 VWAP 跟 CDP 5 線可以參考,缺**日 K 的中短期均線**(MA5 / MA20)作為長期趨勢位置感。
2. 訊號規則對 CDP 5 條線只能「個別欄位 + 比較運算子」設定,沒辦法直接表達兩個常用語意:
   - 「打到任一條 CDP」— 5 條 OR 起來太繁瑣,還只能 `eq`(剛好等於)
   - 「接近 CDP」— 實戰觀察到「沒打到但很接近就反轉」的場景,目前 DSL 無法表達

這份 spec 把兩件事一起設計,但實作切兩個獨立 plan。

## 範圍

**會做**

- IntradayChart 加上日 K MA5 / MA20 兩條水平線,共用一顆 `MA` toggle
- ActiveSignal DSL 新增 `CdpProximityCondition` — 一個合併條件,用 `tolerance_ticks` 控制「打到」(0)或「接近」(>0)的強度

**不做**

- MA10、MA60、分時滾動 MA(per 使用者縮減後決定)
- CDP cross above / cross below 穿越偵測 — tolerance ≥ 1 tick 已可涵蓋跳價場景
- cdp_proximity 與其他 conditions 的細粒度 AND/OR 子群 — 沿用 filter-level `logic` 即可
- 不加 `sma_10` column 進 indicator_cache(縮減後沒用到)

## Feature A:即時走勢 MA5 / MA20

### 資料來源

新 endpoint `GET /api/ma/{symbol}` 從 `indicator_cache` 拿 `sma_5` / `sma_20`(現有 column),回:

```json
{
  "symbol": "2330",
  "sma_5": 1234.5,
  "sma_20": 1210.0,
  "as_of_date": "2026-05-14"
}
```

缺值欄位(剛加入自選、indicator_cache 還沒跑到)回 `null`,前端靜默不畫。

**為什麼是新 endpoint 而不是擴充 candles**

`/api/candles/{symbol}/intraday` 的語意是「分時 K + 昨收」,加 MA 會擰歪職責;`/api/cdp/{symbol}` 已經是獨立 endpoint 的模式,MA 跟著同樣的拆法,日後加 MA10 / MA60 也容易。

**資料時效**

`indicator_cache` 由 cache job 每日跑,週末或 cache job 沒跑時 MA 值會是「上次成功 run 的 date」。`as_of_date` 一起回傳讓前端判斷新舊(本 spec 不在 UI 顯示這欄,但保留欄位給未來)。

### 後端

新檔 `backend/routes/ma.py`:

```python
@router.get("/api/ma/{symbol}")
async def get_ma(symbol: str) -> dict:
    sb = get_supabase()
    if sb.client is None:
        raise HTTPException(503, detail={"error": "supabase_unavailable"})

    latest = await asyncio.to_thread(get_latest_done_run, sb.client)
    if latest is None:
        return {"symbol": symbol, "sma_5": None, "sma_20": None, "as_of_date": None}

    res = await asyncio.to_thread(
        lambda: sb.client.table("indicator_cache")
        .select("sma_5, sma_20")
        .eq("symbol", symbol).eq("date", latest["run_date"])
        .maybe_single().execute()
    )
    row = res.data or {}
    return {
        "symbol": symbol,
        "sma_5": row.get("sma_5"),
        "sma_20": row.get("sma_20"),
        "as_of_date": latest["run_date"],
    }
```

註冊到 `backend/main.py`。

### 前端

**API client(`lib/api.ts`)**

```typescript
export interface MaLevels {
  symbol: string;
  sma_5: number | null;
  sma_20: number | null;
  as_of_date: string | null;
}

export const api = {
  // ...
  ma: (symbol: string) => fetchJSON<MaLevels>(`/api/ma/${symbol}`),
};
```

**IntradayChart 加 MA toggle 跟 render**

- 新 state:`const [showMa, setShowMa] = useLocalToggle("tk:chart:ma", false);`
- 新 state:`const [ma, setMa] = useState<MaLevels | null>(null);`
- useEffect(切 symbol / showMa toggle 時):
  - 切 symbol → 清 ma
  - showMa=true 才 fetch `api.ma(symbol)` 進 state
- 在 `useMemo` 區塊:
  - 計算 `visibleMaKeys`(超出 ±10% 的不畫,沿用 visibleCdpKeys 同套邏輯)
- 在 SVG render 區塊(CDP 線之後、主價線之前)畫 2 條水平虛線 + 右側 margin label
- 樣式需與既有 overlay 區隔:
  - VWAP:`ink-dim`,`strokeDasharray="3 2"`(已存在)
  - CDP:`accent`,`strokeDasharray="4 3"`(已存在)
  - MA 建議:**暖色系給 MA5、中性色系給 MA20**,搭配更稀疏的 `strokeDasharray="2 4"` 點線。實作時若顏色 token 跟既有 overlay 衝突,用 opacity 或不同 dash pattern 進一步區隔 — 具體 token 由 plan 決定
- toggle 按鈕加在 VWAP / CDP / VOL 那一排末尾,文字 `MA`

**錯誤處理**

- `api.ma()` 失敗(network / 503):console.warn,不顯示錯誤 UI(同 VOL,不阻擋主圖)
- 兩個值都 null:不畫線,toggle 仍可點

## Feature B:CdpProximityCondition

### Schema 變更(`backend/models/condition.py`)

新類型:

```python
class CdpProximityCondition(BaseModel):
    levels: list[Literal["ah", "nh", "cdp", "nl", "al"]] = Field(
        default_factory=lambda: ["ah", "nh", "cdp", "nl", "al"],
        min_length=1,
    )
    tolerance_ticks: int = Field(default=0, ge=0, le=10)
```

`ActiveFilter` 擴充:

```python
class ActiveFilter(Filter):
    schema_version: int = 2  # bump 1 → 2
    conditions: list[Condition] = Field(default_factory=list)
    window_conditions: list[WindowCondition] = Field(default_factory=list)
    cdp_proximity: CdpProximityCondition | None = None

    @model_validator(mode="after")
    def conditions_non_empty(self):  # 同名覆蓋 Filter.conditions_non_empty
        if (not self.conditions and not self.window_conditions
                and self.cdp_proximity is None):
            raise ValueError("至少要有一個 condition / window_condition / cdp_proximity")
        return self
```

> Pydantic v2 model_validator 是 *按方法名稱覆蓋*,所以 `ActiveFilter` 的這顆 validator 必須維持 `conditions_non_empty` 這個名字(對應 `Filter` parent 的同名 validator),否則父類別的會繼續跑、邏輯重複。

**Schema 演進**

- 舊 `filter_json`(schema_version=1)沒 `cdp_proximity` 欄位,pydantic 預設 None — 自然向後相容
- 不需要 DB migration:filter_json 是 JSONB,新欄位新增不影響舊 row
- schema_version 升到 2 是文件性的,engine 暫不依賴它做版本分流(只有未來真正破壞性變更時才需要)

### Engine 評估(`backend/services/signal_engine.py`)

`_eval_conditions` 結尾加:

```python
f = active.filter_json
# ...既有的 conditions / window_conditions 評估
cdp_prox = (f.get("cdp_proximity") if isinstance(f, dict)
            else getattr(f, "cdp_proximity", None))
if cdp_prox is not None:
    results.append(self._eval_cdp_proximity(symbol, tick, cdp_prox))
```

新 method:

```python
def _eval_cdp_proximity(self, symbol: str, tick: Tick, prox) -> bool:
    from services.cdp import tick_size  # 公開化後的名稱

    cache = self._field_cache.get(symbol, {})
    levels = prox.get("levels") if isinstance(prox, dict) else prox.levels
    tol_ticks = (prox.get("tolerance_ticks") if isinstance(prox, dict)
                 else prox.tolerance_ticks)
    field_map = {"ah": "cdp_ah", "nh": "cdp_nh", "cdp": "cdp",
                 "nl": "cdp_nl", "al": "cdp_al"}
    for level in levels:
        v = cache.get(field_map[level])
        if v is None:
            continue
        tol = tol_ticks * tick_size(v)
        if abs(tick.price - v) <= tol:
            return True
    return False
```

> `services.cdp._tick_size` 目前是 module-private(底線開頭),這個 spec 把它重新命名為公開 `tick_size`(同時保留同檔內 `_tick_size` 別名給 `round_to_tick_tw` 用,或一起改名)。這是這份 spec 的小重構附帶。

**整合到 filter logic**

- `cdp_proximity` 跟 `conditions` / `window_conditions` 一起 append 進 `results`
- 沿用既有的 `logic = AND/OR` 對整個 results list 做 all/any
- 結果:AND 時三組都要 true,OR 時任一 true

**Cooldown / 重觸發**

不改機制。price 黏在 CDP 附近 → 每 tick 評估都 true → cooldown 期間不重觸發。price 離開 + cooldown 過期後回到附近 → 再觸發一次。這是既有行為。

### UI 變更(`frontend/src/components/ActiveSignalEditor.tsx`)

**API types(`lib/api.ts`)**

```typescript
export interface CdpProximity {
  levels: Array<"ah" | "nh" | "cdp" | "nl" | "al">;
  tolerance_ticks: number;
}

export interface ActiveFilter {
  // ...既有
  cdp_proximity?: CdpProximity | null;
}
```

**新 UI 區塊**

擺在「跨指標條件」之後、「邏輯 / Scope / Cooldown」之前:

```
┌─ CDP 觸發 ────────────────────────────────┐
│ (未啟用)                                  │
│ [+ 啟用 CDP 觸發]                         │
└──────────────────────────────────────────┘
```

啟用後:

```
┌─ CDP 觸發 ────────────────────────────────┐
│ ☑ AH  ☑ NH  ☑ CDP  ☑ NL  ☑ AL    [移除] │
│ Tolerance: [ 0 ] tick                     │
│ (0 = 嚴格打到,>0 = 接近也算)              │
└──────────────────────────────────────────┘
```

**互動**

- `[+ 啟用 CDP 觸發]` 按鈕 → set `cdp_proximity = { levels: 全選, tolerance_ticks: 0 }`
- `[移除]` 按鈕 → set `cdp_proximity = null`
- 5 個 checkbox 各自 toggle `levels` array(至少留 1 個,移除最後 1 個的 checkbox 改為 disabled)
- Tolerance 數字 input,min=0 max=10 step=1

**Validation**

- 全部反選(0 levels)前端 UI 阻擋(最後一個 checkbox disabled),backend pydantic 也擋(min_length=1)
- tolerance 超出 0-10 backend 擋(Field ge=0 le=10),前端 input min/max 也鎖

## 架構決策

1. **不擴充 `Condition` 加 `tolerance` / `cdp_any` 偽欄位** — 雖然改動小,但讓 `Condition` 結構(field+operator+value)變多義,既有的 condition 工具(field 下拉、value 切換常數/欄位)邏輯都得特例處理。獨立 `CdpProximityCondition` 類型語意乾淨。
2. **每個 ActiveSignal 最多 1 個 cdp_proximity**(不是 list)— 一個訊號就是一個語意,多個 cdp_proximity 等於多個 OR'd 觸發條件,用一個就足夠(全選 5 條 + tolerance 已可表達所有實戰需求)。
3. **MA 後端走獨立 endpoint** 而不是擴 candles route — 語意分離,跟 CDP endpoint 風格一致。
4. **MA 視覺樣式留給 plan 決定具體 token**,只在 spec 訂方向(暖色給 MA5、中性給 MA20、稀疏 dash 區隔 VWAP/CDP)— 避免 spec 過早綁實作細節。

## Plan 切分

兩個 plan 獨立、可並行、實作順序不重要。

**Plan 1: 日 MA5 / MA20 線**

- `backend/routes/ma.py`(新)
- `backend/main.py`(register router)
- `frontend/src/lib/api.ts`(api.ma + type)
- `frontend/src/components/IntradayChart.tsx`(state + render + toggle)

**Plan 2: CdpProximityCondition**

- `backend/models/condition.py`(新類型 + ActiveFilter 擴充)
- `backend/services/signal_engine.py`(`_eval_cdp_proximity` + 整合)
- `frontend/src/lib/api.ts`(type)
- `frontend/src/components/ActiveSignalEditor.tsx`(新區塊 UI)

## 測試

**Plan 1**

- 後端:`/api/ma/2330` 回值正確;沒有 indicator_cache 的 symbol 回 null;沒有 cache_runs 也回 null 不 500
- 前端:MA toggle 開關記憶到 localStorage;切 symbol 重新 fetch;兩條線都 null 時 toggle 仍可點(只是沒線可畫)

**Plan 2**

- 後端 unit:`_eval_cdp_proximity` 用各種 (levels, tolerance, tick.price) 組合驗算
- 後端 unit:tolerance_ticks=0 → price 必須 exact 等於 cdp_X 才 true
- 後端 unit:tolerance_ticks=2 → price 在 cdp_X ± 2*tick_size 內 true,外面 false
- 後端 unit:price 在不同價位帶(<10, 10-50, 50-100, 100-500, 500-1000, ≥1000)tick_size 對應正確
- 後端 integration:舊 schema_version=1 filter_json 沒 cdp_proximity 欄位,load 不 raise
- 前端:UI 啟用 / 停用 / 切 levels / 改 tolerance 都 round-trip 到 DB 正確
