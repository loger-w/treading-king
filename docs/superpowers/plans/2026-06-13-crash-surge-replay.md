# 突爆殺(策略 3)實作計畫 — replay 回測擴充 + 上線

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 `replay_engine.py` 能回放 window 條件類規則(餵 ring_buffer),跑突爆殺門檻掃描回測,據此從 UI 建「突爆殺」規則上線。

**Architecture:** 規則本體零程式碼(引擎/前端均已支援 `price_change_pct lt` 負值)。唯一程式碼 = `replay_day` 規則參數化 + 合成 tick 同步餵 ring_buffer。spec 見 `docs/superpowers/specs/2026-06-13-crash-surge-signal-design.md`。

**Tech Stack:** Python(backend/.venv)、pytest + pytest-asyncio、富邦 historical REST(僅 Task 4 回測時登入一次)。

**全程注意:**
- 改 backend 檔案前先確認 user 的 `--reload` dev server 已停(每存檔重啟+重登富邦會觸發登入風暴)。
- pytest 一律用 `backend/.venv` 的 python:在 `backend/` 下跑 `.venv\Scripts\python -m pytest`。
- 已驗證的關鍵事實(不要重查):
  - `patch("services.signal_engine.time.time", ...)` patch 的是全域 `time` 模組的 `time` 屬性(兩個模組 import 同一個 time 模組物件),所以 `ring_buffer.window()` 內的 `time.time()` **同一個 patch 就涵蓋**,不需第二個 patch。
  - `_evaluate` 的正盤 gate 用(被 patch 的)`time.time()`,合成 tick 時間戳在平日 09:00–13:30 內即通過。
  - 純 window 條件的 cooldown level 為空字串 = per(規則, 股票)。
  - 引擎不讀 scope(評估範圍 = monitor_list;replay 直接呼叫 `_evaluate`),scope 給什麼都不影響回放。

---

### Task 0: 建分支

**Files:** 無

- [ ] **Step 1: 從 main 開 feature branch**

```powershell
git checkout -b feat/crash-surge-replay
```

---

### Task 1: `replay_day` 規則參數化 + 餵 ring_buffer(TDD)

**Files:**
- Modify: `backend/scripts/replay_engine.py`(`replay_day`、新增 `touch_rule`/`window_rule`、`main` 的 touch 路徑、`__main__` 區塊)
- Test: `backend/tests/test_replay_engine.py`(新檔)

- [ ] **Step 1: 寫失敗測試**

建立 `backend/tests/test_replay_engine.py`:

```python
"""replay_engine 的 window 條件回放:餵 ring_buffer 後 price_change_pct 規則要會觸發。

合成資料用 2026-06-12(週五)— _evaluate 的正盤 gate 看(被 patch 的)wall-clock,
時間戳必須落在平日 09:00–13:30。前日 H102/L98/C100 → CDP=100、NH=102、NL=98。
"""
import pytest

from scripts.replay_engine import replay_day, touch_rule, window_rule

DAY = "2026-06-12"
PREV = "2026-06-11"


def _data(candles):
    daily = {"6207": {PREV: (102.0, 98.0, 100.0)}}
    minute = {"6207": {DAY: candles}}
    return daily, minute


@pytest.mark.asyncio
async def test_crash_rule_fires_on_5min_drop():
    # 5 根 K 從 100 跌到 97.8(−2.2%),300s 窗 lt −2.0 要觸發
    candles = [
        ("09:01", 100.0, 100.0, 100.0, 100.0),
        ("09:02", 99.6, 99.6, 99.4, 99.4),
        ("09:03", 99.0, 99.0, 98.8, 98.8),
        ("09:04", 98.4, 98.4, 98.2, 98.2),
        ("09:05", 98.0, 98.0, 97.8, 97.8),
    ]
    daily, minute = _data(candles)
    fired = await replay_day(DAY, ["6207"], daily, minute,
                             window_rule("突爆殺", "lt", -2.0, DAY))
    assert fired["6207"] >= 1


@pytest.mark.asyncio
async def test_crash_rule_silent_on_flat_prices():
    candles = [(f"09:0{i}", 100.0, 100.0, 100.0, 100.0) for i in range(1, 6)]
    daily, minute = _data(candles)
    fired = await replay_day(DAY, ["6207"], daily, minute,
                             window_rule("突爆殺", "lt", -2.0, DAY))
    assert sum(fired.values()) == 0


@pytest.mark.asyncio
async def test_touch_rule_still_fires_after_refactor():
    # 99.8 → 100.0 由下碰 CDP(=100)— 回歸:重構不能弄壞碰線回放
    candles = [
        ("09:01", 99.5, 99.5, 99.5, 99.5),
        ("09:02", 99.8, 100.0, 99.8, 100.0),
    ]
    daily, minute = _data(candles)
    fired = await replay_day(DAY, ["6207"], daily, minute, touch_rule(5, DAY))
    assert fired["6207"] >= 1
```

- [ ] **Step 2: 跑測試確認失敗**

```powershell
# 在 backend/ 下
.venv\Scripts\python -m pytest tests\test_replay_engine.py -v
```

預期:收集階段 ImportError(`touch_rule` / `window_rule` 不存在)。
若反而炸在 `sys.stdout.reconfigure`(pytest 捕獲 stdout 的物件可能沒有 reconfigure),屬同一步要修的範圍 — Step 3 會把它移進 `__main__`。

- [ ] **Step 3: 實作重構**

`backend/scripts/replay_engine.py` 改四處:

(a) 刪掉 module-level 第 22 行 `sys.stdout.reconfigure(encoding="utf-8")`,移到檔尾 `__main__`(只有 CLI 需要,import 不該有副作用):

```python
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
```

(b) 新增兩個規則工廠(放在 `candles_to_ticks` 之後):

```python
def touch_rule(rearm_ticks: int, day: str):
    """碰 CDP 規則(沿用 2026-06-12 re-arm 回測的設定)。"""
    from models.condition import ActiveFilter, ActiveSignalOut, CdpProximityCondition
    return ActiveSignalOut(
        id="replay", name="碰CDP",
        filter_json=ActiveFilter(cdp_proximity=CdpProximityCondition(
            levels=["ah", "nh", "cdp", "nl", "al"],
            tolerance_ticks=0, rearm_ticks=rearm_ticks,
        )),
        scope={"type": "watchlist"},
        cooldown_seconds=600, enabled=True, created_at=day,
    )


def window_rule(name: str, operator: str, value: float, day: str):
    """price_change_pct 時窗規則 — 突爆殺(lt 負值)/ 突爆拉(gt 正值)共用。"""
    from models.condition import ActiveFilter, ActiveSignalOut, WindowCondition
    return ActiveSignalOut(
        id="replay", name=name,
        filter_json=ActiveFilter(window_conditions=[WindowCondition(
            type="price_change_pct", window_seconds=300,
            operator=operator, value=value,
        )]),
        scope={"type": "watchlist"},
        cooldown_seconds=1800, enabled=True, created_at=day,
    )
```

(c) `replay_day` 簽名改收規則物件、餵 ring_buffer。整個函式換成:

```python
async def replay_day(day: str, symbols: list[str], daily, minute, active):
    import services.ring_buffer as ring_buffer_module
    from services.cdp import compute_cdp
    from services.ring_buffer import RingBuffer, Tick
    from services.signal_engine import SignalEngine

    engine = SignalEngine()
    engine._active = [active]

    # window 條件讀 ring_buffer 單例 — 每日換全新實例,避免跨日殘留 tick
    ring_buffer_module._default = RingBuffer()
    rb = ring_buffer_module.get_ring_buffer()

    streams = []  # (ts, symbol, price) 全股票合併、按時間序
    for sym in symbols:
        prevs = sorted(d for d in daily.get(sym, {}) if d < day)
        candles = minute.get(sym, {}).get(day, [])
        if not prevs or not candles:
            continue
        h, l, c = daily[sym][prevs[-1]]
        lv = compute_cdp(h, l, c)
        engine._field_cache[sym] = {
            "cdp_ah": lv["ah"], "cdp_nh": lv["nh"], "cdp": lv["cdp"],
            "cdp_nl": lv["nl"], "cdp_al": lv["al"],
        }
        rb.ensure(sym)
        streams.extend((ts, sym, p) for ts, p in candles_to_ticks(day, candles))
    streams.sort()

    fired: dict[str, int] = defaultdict(int)

    async def fake_broadcast(payload):
        fired[payload["data"]["symbol"]] += 1

    clock = [0.0]
    # 此 patch 改的是全域 time 模組的 time 屬性 — signal_engine 與 ring_buffer
    # import 同一個 time 模組物件,ring_buffer.window() 的 cutoff 一併用假時鐘
    with patch("services.signal_engine.time.time", side_effect=lambda: clock[0]), \
         patch("services.signal_engine.get_broadcaster") as mock_bc, \
         patch("services.signal_engine.get_signal_writer") as mock_sw:
        mock_bc.return_value.broadcast = fake_broadcast
        mock_sw.return_value = MagicMock()
        for ts, sym, price in streams:
            clock[0] = ts
            tick = Tick(price=price, size=1, time=ts)
            rb.append(sym, tick)
            await engine._evaluate(sym, tick)
    return fired
```

(scope 由 `{"type": "symbols", ...}` 改成 watchlist:引擎不讀 scope,回放行為不變,工廠函式因此不必收 symbols。)

(d) `main()` 內 touch 路徑兩處呼叫改用工廠:

```python
        f0 = await replay_day(day, day_syms[day], daily, minute, touch_rule(0, day))
        fN = await replay_day(day, day_syms[day], daily, minute, touch_rule(args.rearm, day))
```

- [ ] **Step 4: 跑測試確認通過**

```powershell
.venv\Scripts\python -m pytest tests\test_replay_engine.py -v
```

預期:3 passed。

- [ ] **Step 5: 跑全套測試確認無回歸**

```powershell
.venv\Scripts\python -m pytest -q
```

預期:全綠(若有既有失敗,需確認與本次改動無關並回報)。

- [ ] **Step 6: Commit**

```powershell
git add backend/scripts/replay_engine.py backend/tests/test_replay_engine.py
git commit -m "feat(replay): replay_day 規則參數化 + 餵 ring_buffer,window 條件可回放"
```

---

### Task 2: `--preset crash` 門檻掃描 CLI

**Files:**
- Modify: `backend/scripts/replay_engine.py`(`main()`、module docstring)

main() 是純印表 glue,replay_day 已有單元測試 — 本 task 不另寫測試,以 Task 3 真實回測當煙霧驗證。

- [ ] **Step 1: 實作 CLI**

`main()` 加參數與 crash 分支:

```python
async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--rearm", type=int, default=5)
    ap.add_argument("--preset", choices=["touch", "crash"], default="touch")
    args = ap.parse_args()

    day_syms = day_symbols_from_log(args.days)
    if not day_syms:
        print("signals_log 無資料")
        return
    all_syms = sorted({s for v in day_syms.values() for s in v})
    days = sorted(day_syms)
    daily, minute = fetch_fubon(all_syms, days[0], days[-1])

    if args.preset == "crash":
        await run_crash(days, day_syms, daily, minute)
        return

    # --- touch(現狀)---
    print(f"\n{'day':<12}{'rearm=0':>9}{'rearm=' + str(args.rearm):>9}")
    tot0 = totN = 0
    last_detail = {}
    for day in days:
        f0 = await replay_day(day, day_syms[day], daily, minute, touch_rule(0, day))
        fN = await replay_day(day, day_syms[day], daily, minute, touch_rule(args.rearm, day))
        print(f"{day:<12}{sum(f0.values()):>9}{sum(fN.values()):>9}")
        tot0 += sum(f0.values())
        totN += sum(fN.values())
        last_detail = {s: (f0.get(s, 0), fN.get(s, 0)) for s in day_syms[day]}
    print(f"{'total':<12}{tot0:>9}{totN:>9}")
    print(f"\n-- {days[-1]} per-symbol (rearm=0 → rearm={args.rearm}) --")
    for s, (a, b) in sorted(last_detail.items()):
        print(f"{s:<6}{a:>4} → {b}")
```

新增 `run_crash`(放在 `main` 之前):

```python
CRASH_THRESHOLDS = [-1.5, -2.0, -2.5, -3.0]


async def run_crash(days, day_syms, daily, minute):
    """突爆殺門檻掃描 + 突爆拉(gt 2.0)同池對照。"""
    cols = [f"lt{t}" for t in CRASH_THRESHOLDS] + ["gt2.0"]
    print(f"\n{'day':<12}" + "".join(f"{c:>9}" for c in cols))
    totals = [0] * len(cols)
    last_detail = {}
    for day in days:
        runs = []
        for thr in CRASH_THRESHOLDS:
            runs.append(await replay_day(day, day_syms[day], daily, minute,
                                         window_rule("突爆殺", "lt", thr, day)))
        runs.append(await replay_day(day, day_syms[day], daily, minute,
                                     window_rule("突爆拉", "gt", 2.0, day)))
        counts = [sum(f.values()) for f in runs]
        totals = [a + b for a, b in zip(totals, counts)]
        print(f"{day:<12}" + "".join(f"{c:>9}" for c in counts))
        base, ref = runs[1], runs[-1]   # lt-2.0 基線 vs 突爆拉對照
        last_detail = {s: (base.get(s, 0), ref.get(s, 0)) for s in day_syms[day]}
    print(f"{'total':<12}" + "".join(f"{c:>9}" for c in totals))
    print(f"\n-- {days[-1]} per-symbol (突爆殺 lt-2.0 → 突爆拉 gt2.0) --")
    for s, (a, b) in sorted(last_detail.items()):
        print(f"{s:<6}{a:>4} → {b}")
```

- [ ] **Step 2: 更新 module docstring 用法**

docstring 用法區加一行:

```
  .venv\\Scripts\\python scripts\\replay_engine.py --preset crash  # 突爆殺門檻掃描
```

- [ ] **Step 3: 跑全套測試(確認 main 改動沒弄壞 import)**

```powershell
.venv\Scripts\python -m pytest -q
```

- [ ] **Step 4: Commit**

```powershell
git add backend/scripts/replay_engine.py
git commit -m "feat(replay): --preset crash 突爆殺門檻掃描(含突爆拉 gt2.0 同池對照)"
```

---

### Task 3: 跑 5 日回測 + 記錄結果

**Files:**
- Create: `docs/notes/2026-06-13-crash-backtest.md`

- [ ] **Step 1: 確認 dev server 已停**

跑回測會 `apikey_login` 富邦一次;dev server 同時跑會多一份登入。向 user 確認已停(或確認沒在跑)再執行。

- [ ] **Step 2: 跑回測**

```powershell
# 在 backend/ 下;每股票 2 次 historical 呼叫、各 sleep 1.1s,股票多時要幾分鐘
.venv\Scripts\python scripts\replay_engine.py --preset crash
```

預期輸出:5 日 × 5 欄(lt−1.5 / lt−2.0 / lt−2.5 / lt−3.0 / gt2.0)訊號量表 + total 列 + 最後一日 per-symbol 明細。

- [ ] **Step 3: 寫回測紀錄**

結果表 + 解讀(各門檻 vs 突爆拉吵度、明細裡的代表案例,例如 2327 是否被抓到)寫進 `docs/notes/2026-06-13-crash-backtest.md`。

- [ ] **Step 4: Commit**

```powershell
git add docs/notes/2026-06-13-crash-backtest.md
git commit -m "docs(notes): 突爆殺 5 日回測結果(門檻掃描 + 突爆拉對照)"
```

- [ ] **Step 5: USER GATE — 呈現結果,user 決定最終門檻**

把表格與建議呈現給 user。**未拿到門檻決定前不進 Task 4。**

---

### Task 4: UI 建「突爆殺」規則上線(零程式碼)

**Files:** 無(規則存在 `backend/data/config.json`,由前端 UI 寫入)

- [ ] **Step 1: 提供 user 建規則的精確設定**

前端「訊號規則」對話框新增:

| 欄位 | 值 |
|---|---|
| 名稱 | 突爆殺 |
| 時窗條件 | 漲跌幅 % / 5 分鐘 / `<` / **Task 3 定案門檻**(基線 −2.0) |
| cooldown | 30 分鐘 |
| Discord 通知 | 開 |

- [ ] **Step 2: 驗證規則已寫入**

user 建好後,確認 `backend/data/config.json` 的 `active_signals` 多了突爆殺(window_conditions: `price_change_pct / lt / 300`),enabled=true。

- [ ] **Step 3: 上線後驗證項(掛起,非本 session 完成)**

盤中遇 5 分鐘急殺(國巨 6/12 型)能推 Discord 圖卡 — 列入盤中實測清單。

---

### Task 5: 收尾 — PR

- [ ] **Step 1: 用 superpowers:finishing-a-development-branch 流程**

分支 `feat/crash-surge-replay` → push → `gh pr create`(對 main)。PR 內容:replay 擴充 + 測試 + 回測紀錄;規則本體不在 diff 裡(UI 寫入 config.json,本機資料檔不進 git — 以 repo 現況為準,config.json 若本來就被追蹤則一併確認 user 是否要把規則 commit)。
