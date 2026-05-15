# CdpProximityCondition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ActiveSignal DSL 新增 `CdpProximityCondition` —「打到 / 接近任一 CDP 線」合併條件,spec 見 `docs/superpowers/specs/2026-05-15-ma-cdp-proximity-design.md`。

**Architecture:** `ActiveFilter` 加新欄位 `cdp_proximity: CdpProximityCondition | None`,signal_engine 評估時把這顆當第三組條件 append 進 `results`,沿用既有 AND/OR `logic`。不需要 DB migration(filter_json 是 JSONB,新欄位向後相容)。

**Tech Stack:** FastAPI + Python 3.12 + Pydantic v2(backend),React 18 + TypeScript + Vite + Tailwind 3(frontend),Supabase JSONB(`active_signals.filter_json`)。

**Test Strategy:** Codebase 無測試基建(無 `tests/` 目錄、無 vitest),沿用既有 plan convention。Backend 改動以 `curl` smoke test 驗證 + 觀察 `_eval_cdp_proximity` 各 branch;Frontend 改動以 `npm run build`(型別檢查) + browser 手動 smoke 驗證。

**Reference paths:**
- Backend root: `C:\side-project\treading-king\backend`
- Frontend root: `C:\side-project\treading-king\frontend`

**啟動指令(多次會用):**
- Backend dev: `cd backend; .venv\Scripts\activate; uvicorn main:app --reload --port 8000`
- Frontend dev: `cd frontend; npm run dev`
- Frontend build: `cd frontend; npm run build`

---

## Task 1: Backend — `cdp.py` 把 `_tick_size` 改成 public `tick_size`

**Files:**
- Modify: `backend/services/cdp.py`

理由:`signal_engine._eval_cdp_proximity` 需要跨檔用 tick_size 算 tolerance,目前底線開頭是 module-private。

- [ ] **Step 1: 改 `cdp.py` 內 `_tick_size` 改成 `tick_size`**

定位約 32-37 行,把 function 改名:

```python
def tick_size(price: float) -> float:
    """回傳 price 對應的台股最小升降單位。"""
    for upper, tick in _TICK_LADDER:
        if price < upper:
            return tick
    return 5.00  # unreachable
```

- [ ] **Step 2: 改檔內所有 caller**

定位約 48 行的 `tick = _tick_size(price)`(在 `round_to_tick_tw` 內),改成:

```python
    tick = tick_size(price)
```

Grep 確認檔內沒其他 `_tick_size` 引用:

```powershell
cd C:\side-project\treading-king\backend\services
Select-String -Pattern '_tick_size' -Path cdp.py
```

預期:0 hits(全改完)。

- [ ] **Step 3: Grep 整個 backend 確認沒外部 caller 用舊名**

```powershell
cd C:\side-project\treading-king\backend
Select-String -Pattern '_tick_size' -Path *.py,services/*.py,routes/*.py,models/*.py
```

預期:0 hits。

- [ ] **Step 4: Backend 重啟驗證沒 import error**

```powershell
cd C:\side-project\treading-king\backend
.\.venv\Scripts\activate
uvicorn main:app --reload --port 8000
```

預期:啟動成功 log `Startup done`,沒 `ImportError` / `AttributeError`。手動拉 `/api/cdp/2330` 確認 CDP 還能算:

```powershell
$key = (Get-Content C:\side-project\treading-king\backend\.env | Select-String '^BFF_API_KEY=').ToString().Split('=',2)[1]
curl.exe http://localhost:8000/api/cdp/2330 -H "X-API-Key: $key"
```

預期:HTTP 200,5 條 CDP 線值正常。

- [ ] **Step 5: Commit**

```powershell
cd C:\side-project\treading-king
git add backend/services/cdp.py
git commit -m "refactor(backend): expose cdp.tick_size for cross-module use"
```

---

## Task 2: Backend — `condition.py` 加 `CdpProximityCondition` + extend `ActiveFilter`

**Files:**
- Modify: `backend/models/condition.py`

- [ ] **Step 1: 加 `CdpProximityCondition` 類型**

定位 `WindowCondition` 類別之後、`ActiveFilter` 類別之前(約 120-122 行),加:

```python
class CdpProximityCondition(BaseModel):
    """CDP 觸發條件 — tick price 落在所選 CDP 線的 ±N tick 範圍內。

    levels: 要監看的 CDP 線(5 條任意組合,預設全選)
    tolerance_ticks:
      - 0  → 嚴格「打到」(tick.price == cdp_X,已 round to tick 所以可 exact match)
      - >0 → 「接近」(tick.price 在 cdp_X ± tolerance × tick_size(cdp_X) 內)
    """

    levels: list[Literal["ah", "nh", "cdp", "nl", "al"]] = Field(
        default_factory=lambda: ["ah", "nh", "cdp", "nl", "al"],
        min_length=1,
    )
    tolerance_ticks: int = Field(default=0, ge=0, le=10)
```

- [ ] **Step 2: 擴充 `ActiveFilter`**

定位既有 `ActiveFilter` class(約 123-137 行),整個取代為:

```python
class ActiveFilter(Filter):
    """即時訊號專用 Filter — 在 Filter 之上加時窗條件 + CDP 觸發條件。

    跟 Filter 的差異:允許 conditions=[] 當 window_conditions 或 cdp_proximity 非空
    (即時訊號可單獨用其中任一種觸發機制)。
    """

    schema_version: int = 2  # bump from 1 → 2 (加了 cdp_proximity 欄位)
    window_conditions: list[WindowCondition] = Field(default_factory=list)
    cdp_proximity: CdpProximityCondition | None = None

    @model_validator(mode="after")
    def conditions_non_empty(self):
        # 覆蓋 Filter.conditions_non_empty:允許 conditions=[] 當 window_conditions 或 cdp_proximity 非空
        if (not self.conditions
                and not self.window_conditions
                and self.cdp_proximity is None):
            raise ValueError("至少要有一個 condition / window_condition / cdp_proximity")
        return self
```

> 重要:method name 必須維持 `conditions_non_empty`(同名覆蓋 Filter parent 的 validator),否則 Pydantic v2 會繼續跑 parent 的版本,造成新規則被舊規則擋下。

- [ ] **Step 3: Backend 重啟驗證 pydantic 不 raise**

```powershell
cd C:\side-project\treading-king\backend
.\.venv\Scripts\activate
uvicorn main:app --reload --port 8000
```

預期:啟動成功,沒 pydantic schema error。

- [ ] **Step 4: Smoke test — 舊 schema_version=1 filter_json 載入正常**

```powershell
$key = (Get-Content C:\side-project\treading-king\backend\.env | Select-String '^BFF_API_KEY=').ToString().Split('=',2)[1]
curl.exe http://localhost:8000/api/active_signals -H "X-API-Key: $key"
```

預期:HTTP 200,既有 active_signals 全部正常回傳(每個 row 的 `filter_json.cdp_proximity` 為 null 或不存在,pydantic 預設 None 不 raise)。

- [ ] **Step 5: Smoke test — 建立帶 cdp_proximity 的新 active_signal**

```powershell
curl.exe -X POST http://localhost:8000/api/active_signals `
  -H "X-API-Key: $key" -H "Content-Type: application/json" `
  -d '{\"name\":\"test_cdp_touch\",\"filter_json\":{\"schema_version\":2,\"conditions\":[],\"window_conditions\":[],\"logic\":\"AND\",\"cdp_proximity\":{\"levels\":[\"ah\",\"nh\",\"cdp\",\"nl\",\"al\"],\"tolerance_ticks\":0}},\"scope\":{\"type\":\"watchlist\"},\"cooldown_seconds\":1800,\"enabled\":false}'
```

預期:HTTP 200/201,回新建的 active_signal 物件,`filter_json.cdp_proximity.tolerance_ticks` = 0。

- [ ] **Step 6: Smoke test — validation 阻擋空 levels**

```powershell
curl.exe -X POST http://localhost:8000/api/active_signals `
  -H "X-API-Key: $key" -H "Content-Type: application/json" `
  -d '{\"name\":\"bad\",\"filter_json\":{\"schema_version\":2,\"conditions\":[],\"window_conditions\":[],\"logic\":\"AND\",\"cdp_proximity\":{\"levels\":[],\"tolerance_ticks\":0}},\"scope\":{\"type\":\"watchlist\"},\"cooldown_seconds\":1800,\"enabled\":false}'
```

預期:HTTP 422(pydantic min_length=1 擋下)。

- [ ] **Step 7: Smoke test — validation 阻擋 tolerance 超界**

```powershell
curl.exe -X POST http://localhost:8000/api/active_signals `
  -H "X-API-Key: $key" -H "Content-Type: application/json" `
  -d '{\"name\":\"bad2\",\"filter_json\":{\"schema_version\":2,\"conditions\":[],\"window_conditions\":[],\"logic\":\"AND\",\"cdp_proximity\":{\"levels\":[\"ah\"],\"tolerance_ticks\":99}},\"scope\":{\"type\":\"watchlist\"},\"cooldown_seconds\":1800,\"enabled\":false}'
```

預期:HTTP 422(Field le=10 擋下)。

- [ ] **Step 8: 清掉 smoke test 建的 record**

從上面 step 5 的 response 拿 `id`,然後:

```powershell
$id = "<paste-id-from-step-5>"
curl.exe -X DELETE "http://localhost:8000/api/active_signals/$id" -H "X-API-Key: $key"
```

預期:HTTP 200/204。

- [ ] **Step 9: Commit**

```powershell
cd C:\side-project\treading-king
git add backend/models/condition.py
git commit -m "feat(backend): add CdpProximityCondition to ActiveFilter DSL"
```

---

## Task 3: Backend — `signal_engine.py` 加 `_eval_cdp_proximity` + 整合

**Files:**
- Modify: `backend/services/signal_engine.py`

- [ ] **Step 1: 加 `_eval_cdp_proximity` method**

定位既有 `_eval_filter_cond` method 結尾(約 326 行),其後加新 method:

```python
    def _eval_cdp_proximity(self, symbol: str, tick: Tick, prox) -> bool:
        """tick.price 落在所選 CDP 線的 ±N tick 範圍內就 true。

        prox 可以是 dict(從 filter_json JSON 讀)或 Pydantic CdpProximityCondition。
        """
        from services.cdp import tick_size

        cache = self._field_cache.get(symbol, {})
        levels = prox.get("levels") if isinstance(prox, dict) else prox.levels
        tol_ticks = (prox.get("tolerance_ticks") if isinstance(prox, dict)
                     else prox.tolerance_ticks)

        field_map = {
            "ah": "cdp_ah", "nh": "cdp_nh", "cdp": "cdp",
            "nl": "cdp_nl", "al": "cdp_al",
        }
        for level in levels:
            v = cache.get(field_map[level])
            if v is None:
                continue
            tol = tol_ticks * tick_size(v)
            if abs(tick.price - v) <= tol:
                return True
        return False
```

- [ ] **Step 2: 整合進 `_eval_conditions`**

定位 `_eval_conditions` method(約 266-277 行),整個 body 取代為:

```python
    def _eval_conditions(self, active: ActiveSignalOut, symbol: str, tick: Tick) -> bool:
        # WindowCondition + Filter.conditions + CdpProximity
        f = active.filter_json
        results: list[bool] = []
        for wc in (f.get("window_conditions") if isinstance(f, dict) else getattr(f, "window_conditions", [])):
            results.append(self._eval_window(symbol, tick, wc))
        for c in (f.get("conditions") if isinstance(f, dict) else getattr(f, "conditions", [])):
            results.append(self._eval_filter_cond(symbol, tick, c))
        cdp_prox = (f.get("cdp_proximity") if isinstance(f, dict)
                    else getattr(f, "cdp_proximity", None))
        if cdp_prox is not None:
            results.append(self._eval_cdp_proximity(symbol, tick, cdp_prox))
        if not results:
            return False
        logic = (f.get("logic") if isinstance(f, dict) else getattr(f, "logic", "AND"))
        return all(results) if logic == "AND" else any(results)
```

- [ ] **Step 3: Backend 重啟確認載入沒 import error**

```powershell
cd C:\side-project\treading-king\backend
.\.venv\Scripts\activate
uvicorn main:app --reload --port 8000
```

預期:啟動成功,log 看到 `SignalEngine started`,沒 traceback。

- [ ] **Step 4: 手動行為 smoke test(透過真實 active_signal 跑一次)**

A. 從 step 5 of Task 2 起一條 enabled=true 的 cdp_proximity 規則(改 enabled 為 true),scope 用 `{"type":"symbols","symbols":["2330"]}` 限縮:

```powershell
$key = (Get-Content C:\side-project\treading-king\backend\.env | Select-String '^BFF_API_KEY=').ToString().Split('=',2)[1]
curl.exe -X POST http://localhost:8000/api/active_signals `
  -H "X-API-Key: $key" -H "Content-Type: application/json" `
  -d '{\"name\":\"engine_smoke\",\"filter_json\":{\"schema_version\":2,\"conditions\":[],\"window_conditions\":[],\"logic\":\"AND\",\"cdp_proximity\":{\"levels\":[\"ah\",\"nh\",\"cdp\",\"nl\",\"al\"],\"tolerance_ticks\":10}},\"scope\":{\"type\":\"symbols\",\"symbols\":[\"2330\"]},\"cooldown_seconds\":60,\"enabled\":true}'
```

> tolerance_ticks=10 + cooldown 60s 讓真實盤中 tick 容易觸發,方便觀察。

B. 看 backend log,確認規則 reload:

預期:log 出現 `active_signals reloaded: N enabled`(N 比之前多 1)。

C. 等 1-2 分鐘看 log。盤中時段 2330 有 tick 持續進來時,如果 tick.price 落在任一 CDP ±10 tick 內 → 應該看到 `signal triggered`-style log 或 broadcaster.broadcast 被叫。盤後測試的話可以暫不會觸發,但**至少要確認沒有 traceback / `_eval_cdp_proximity` 被叫的 KeyError**。

D. 清掉測試 record:

```powershell
$id = "<paste-id-from-step-A>"
curl.exe -X DELETE "http://localhost:8000/api/active_signals/$id" -H "X-API-Key: $key"
```

- [ ] **Step 5: 手算驗證 — 不同價位帶 tick_size 是否正確**

打開 Python REPL(在 backend venv 內):

```powershell
cd C:\side-project\treading-king\backend
.\.venv\Scripts\activate
python
```

```python
from services.cdp import tick_size
assert tick_size(9.5) == 0.01     # < 10
assert tick_size(45) == 0.05      # 10-50
assert tick_size(95) == 0.10      # 50-100
assert tick_size(450) == 0.50     # 100-500
assert tick_size(900) == 1.00     # 500-1000
assert tick_size(1500) == 5.00    # >= 1000
print("tick_size OK")
exit()
```

預期:全部 assert 通過,印 `tick_size OK`。

- [ ] **Step 6: Commit**

```powershell
cd C:\side-project\treading-king
git add backend/services/signal_engine.py
git commit -m "feat(backend): wire CdpProximityCondition into signal engine evaluator"
```

---

## Task 4: Frontend — `api.ts` type 擴充

**Files:**
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: 加 `CdpProximity` type**

定位既有 `WindowCondition` interface(約 94-99 行),其後加:

```typescript
export interface CdpProximity {
  levels: Array<"ah" | "nh" | "cdp" | "nl" | "al">;
  tolerance_ticks: number;
}
```

- [ ] **Step 2: 擴充 `ActiveFilter` interface**

定位既有 `ActiveFilter` interface(約 101-103 行),整個取代為:

```typescript
export interface ActiveFilter extends Filter {
  // schema_version 已從 Filter inherit,不重複宣告
  window_conditions?: WindowCondition[];
  cdp_proximity?: CdpProximity | null;
}
```

- [ ] **Step 3: 型別檢查通過**

```powershell
cd C:\side-project\treading-king\frontend
npm run build
```

預期:build 成功,沒 TS error。

- [ ] **Step 4: Commit**

```powershell
cd C:\side-project\treading-king
git add frontend/src/lib/api.ts
git commit -m "feat(frontend): add CdpProximity type + extend ActiveFilter"
```

---

## Task 5: Frontend — `ActiveSignalEditor` 加 CDP 觸發區塊

**Files:**
- Modify: `frontend/src/components/ActiveSignalEditor.tsx`

- [ ] **Step 1: 加 import**

定位檔頂 import 區(約 1-6 行),把 `CdpProximity` 加進 type imports:

```typescript
import {
  ALL_FIELDS, api, type ActiveFilter, type ActiveSignal, type CdpProximity,
  type Condition, type ConditionField, type ConditionOperator,
  type Scope, type WindowCondition, type WindowConditionType, type WindowSeconds,
} from "../lib/api";
```

- [ ] **Step 2: 加 CDP level label 常數**

定位既有 `WINDOW_TYPE_LABEL` 常數(約 28-30 行),其後加:

```typescript
const CDP_LEVEL_LABEL: Record<"ah" | "nh" | "cdp" | "nl" | "al", string> = {
  ah: "AH (最高值)", nh: "NH (近高)", cdp: "CDP 中線",
  nl: "NL (近低)", al: "AL (最低值)",
};

const ALL_CDP_LEVELS = ["ah", "nh", "cdp", "nl", "al"] as const;
```

- [ ] **Step 3: 加 cdp_proximity helper functions**

定位既有 `removeCond` 函式(約 77-79 行)之後,加:

```typescript
  function enableCdpProx() {
    setFilter({
      ...filter,
      cdp_proximity: { levels: [...ALL_CDP_LEVELS], tolerance_ticks: 0 },
    });
  }
  function disableCdpProx() {
    setFilter({ ...filter, cdp_proximity: null });
  }
  function toggleCdpLevel(level: typeof ALL_CDP_LEVELS[number]) {
    const prox = filter.cdp_proximity;
    if (!prox) return;
    const checked = prox.levels.includes(level);
    let next: typeof prox.levels;
    if (checked) {
      if (prox.levels.length <= 1) return;  // 至少留 1 個
      next = prox.levels.filter((l) => l !== level);
    } else {
      next = [...prox.levels, level];
    }
    setFilter({ ...filter, cdp_proximity: { ...prox, levels: next } });
  }
  function updateCdpTolerance(tol: number) {
    const prox = filter.cdp_proximity;
    if (!prox) return;
    const clamped = Math.max(0, Math.min(10, Math.round(tol)));
    setFilter({ ...filter, cdp_proximity: { ...prox, tolerance_ticks: clamped } });
  }
```

- [ ] **Step 4: 放寬 save() 的「至少一條條件」檢查**

定位 `save()` 函式內的 check(約 82-85 行),把:

```typescript
    if (filter.conditions.length === 0 && (filter.window_conditions ?? []).length === 0) {
      setError("至少要有一條條件"); return;
    }
```

改為:

```typescript
    if (filter.conditions.length === 0
        && (filter.window_conditions ?? []).length === 0
        && !filter.cdp_proximity) {
      setError("至少要有一條條件"); return;
    }
```

- [ ] **Step 5: 加 CDP 觸發 UI 區塊**

定位「跨指標條件」區塊結尾(`<button type="button" onClick={addCond} ...>+ 新增條件</button>` 那個 `</div>`,約 178-179 行),之後、「Logic / Scope / Cooldown」區塊之前,插入新區塊:

```typescript
        {/* CDP 觸發區塊 */}
        <div className="border-t border-line pt-3 mb-4">
          <div className="label-tiny mb-2">CDP 觸發</div>
          <p className="text-2xs text-ink-dim mb-3 leading-relaxed">
            價格打到(或接近)任一所選 CDP 線即觸發。Tolerance = 0 為嚴格打到,&gt;0 為 ± N tick 內也算。
          </p>
          {filter.cdp_proximity === null || filter.cdp_proximity === undefined ? (
            <button type="button" onClick={enableCdpProx}
              className="text-xs text-ink-dim hover:text-accent border border-dashed border-line px-3 py-1">
              + 啟用 CDP 觸發
            </button>
          ) : (
            <div className="border border-line p-3 space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                {ALL_CDP_LEVELS.map((lv) => {
                  const checked = filter.cdp_proximity!.levels.includes(lv);
                  const isLastChecked = checked && filter.cdp_proximity!.levels.length === 1;
                  return (
                    <label key={lv} className={`text-sm flex items-center gap-1 ${isLastChecked ? "opacity-60" : "cursor-pointer"}`}>
                      <input type="checkbox" checked={checked}
                        disabled={isLastChecked}
                        onChange={() => toggleCdpLevel(lv)}
                        className="accent-accent" />
                      {CDP_LEVEL_LABEL[lv]}
                    </label>
                  );
                })}
                <button type="button" onClick={disableCdpProx}
                  className="ml-auto text-ink-dim hover:text-bear text-xs">移除</button>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-sm text-ink-muted">Tolerance:</span>
                <input type="number" min={0} max={10} step={1}
                  value={filter.cdp_proximity.tolerance_ticks}
                  onChange={(e) => updateCdpTolerance(Number(e.target.value))}
                  className="bg-bg-deep border border-line text-sm px-2 py-1 w-20 tabular-nums" />
                <span className="text-xs text-ink-dim">tick (0 = 嚴格打到,&gt;0 = 接近也算)</span>
              </div>
            </div>
          )}
        </div>
```

- [ ] **Step 6: 型別檢查通過**

```powershell
cd C:\side-project\treading-king\frontend
npm run build
```

預期:build 成功,沒 TS error。

- [ ] **Step 7: Browser smoke test**

```powershell
cd C:\side-project\treading-king\frontend
npm run dev
```

開 <http://localhost:5173>,進訊號規則編輯頁(SignalRulesDialog):

1. 「新增訊號規則」→ 跨指標條件留空、即時時窗條件留空
2. 看到「CDP 觸發」區塊,點 `+ 啟用 CDP 觸發`
3. 預期顯示 5 個 checkbox(全勾)+ Tolerance input(0)
4. 取消其中 4 個,只留 1 個 → 最後一個 checkbox 應 disabled(不能再取消、灰色)
5. Tolerance 輸入 99 → 自動 clamp 成 10;輸入 -5 → 自動 clamp 成 0
6. 名稱填 `cdp_smoke_test`,Scope 選自選清單,Cooldown 60s,「儲存並啟用」
7. F12 → network panel 看 `POST /api/active_signals`,payload `filter_json.cdp_proximity` 含 `{ levels: [...], tolerance_ticks: 0~10 }`,response 200
8. 進「編輯」這條規則,看到 CDP 觸發區塊回填正確
9. 點「移除」→ 區塊收回成 `+ 啟用 CDP 觸發` 按鈕,儲存後 `filter_json.cdp_proximity` 為 null
10. 砍掉測試規則(右上「停用」/ 「刪除」按鈕,看 UI 有沒有提供;沒有的話用 curl DELETE)

- [ ] **Step 8: Commit**

```powershell
cd C:\side-project\treading-king
git add frontend/src/components/ActiveSignalEditor.tsx
git commit -m "feat(frontend): add CDP proximity condition UI block to ActiveSignalEditor"
```

---

## Task 6: End-to-end smoke — 真的設一條 CDP 規則跑跑看

**Files:** (none — manual integration test)

- [ ] **Step 1: 用 UI 建一條實戰用的 CDP 規則**

進 SignalRulesDialog → 新增:
- 名稱:`CDP_proximity_live`
- 跨指標條件:留空
- 即時時窗條件:留空
- CDP 觸發:5 條全勾,Tolerance = 3
- Scope:自選清單
- Cooldown:300s
- 儲存並啟用

- [ ] **Step 2: 觀察 30 分鐘(盤中時段)**

進 Monitor 頁,把自選清單中股價接近 CDP 任一條的 symbol 留著看。預期:當價格進入 cdp ±3 tick 範圍時,Signal Chip / TriggerList 出現觸發紀錄。

- [ ] **Step 3: 驗證觸發紀錄包含正確欄位**

進 `signals_history` 頁面(或 curl):

```powershell
$key = (Get-Content C:\side-project\treading-king\backend\.env | Select-String '^BFF_API_KEY=').ToString().Split('=',2)[1]
curl.exe "http://localhost:8000/api/signals/history?active_signal_id=<paste-id>&limit=10" -H "X-API-Key: $key"
```

預期:回傳 row(s) 含 `trigger_price` ≈ 某個 CDP 值 ±3 tick 範圍內。

- [ ] **Step 4: 砍掉測試規則**

```powershell
$id = "<active_signal id>"
curl.exe -X DELETE "http://localhost:8000/api/active_signals/$id" -H "X-API-Key: $key"
```

或從 UI 「停用 / 刪除」。

---

## Done

CDP 觸發條件上線。實戰用法:

- **嚴格打到** = tolerance 0,5 條全勾,適合精確進場
- **接近反轉** = tolerance 2-3,5 條全勾,適合早一步抓「沒打到但快到了」的回轉
- **只看上下界** = tolerance 0-3,只勾 AH / AL,抓極端突破或回測
