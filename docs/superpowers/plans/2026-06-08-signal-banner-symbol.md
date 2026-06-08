# 訊號橫幅加標的＋CDP 中軸正名 — 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 訊號觸發橫幅第一行顯示股票「名稱 代號」(查不到名稱退化成只有代號),並把中軸線觸發從「碰 CDP CDP」正名為「碰 CDP 中軸」。

**Architecture:** 後端在 `_fanout` 用既有記憶體查表 `market.get_symbol(symbol)` 取名稱,隨 `send_signal` 進 bot 的 localhost push payload(獨立於圖卡抓取,故 fallback 純文字也有名稱);bot 端 `signal.ts` 解析選填 `name`、在 `formatBanner` 第一行插入標的、`levelLabel` 對 CDP 中軸線回「中軸」。

**Tech Stack:** Python (FastAPI / pytest / httpx mock)、TypeScript (Node bot / vitest / discord.js)。

**對應 spec:** `docs/superpowers/specs/2026-06-08-signal-banner-symbol-design.md`

---

## 環境前置(本 worktree 已就緒,僅供新 session 參考)

- 分支:`fix/signal-banner-symbol`(off main),worktree:`C:/side-project/treading-king/.claude/worktrees/signal-banner-symbol`。
- **bot 依賴**:已 `npm install --no-package-lock` 安裝(此 worktree 的 `package-lock.json` 在 main 上就與 `package.json` 不同步,`npm ci` 會失敗 —— 既有問題,本計畫不動 lockfile)。
- **backend 無 venv**:借主 repo 的 venv python 跑測試。本計畫所有 `PYTEST` 指令 =
  `C:/side-project/treading-king/backend/.venv/Scripts/python.exe -m pytest`,工作目錄 `…/signal-banner-symbol/backend`。
- **bot 測試**:工作目錄 `…/signal-banner-symbol/bot`,`npx vitest run <檔>`(聚焦)或 `npm test`(全部)。

每個 Task 結束都 commit;commit 訊息末行帶
`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

---

## Task 1:後端 `send_signal` 接收並轉送 `name`

**Files:**
- Modify: `backend/services/discord_notifier.py`(`send_signal` 簽名 + payload)
- Test: `backend/tests/test_discord_notifier.py`

- [ ] **Step 1:改測試 — 既有 POST 測試加 name 斷言 + 新增「無 name → null」測試**

在 `test_send_signal_posts_to_bot_when_url_set` 的 `send_signal(...)` 呼叫加一個 kwarg `name="台積電"`(放在 `symbol="2330",` 之後):

```python
        await discord_notifier.send_signal(
            rule_name="漲停打開碰CDP",
            symbol="2330",
            name="台積電",
            price=600.0,
            volume=10,
            triggered_at_iso="2026-06-08T05:30:00+00:00",
            cdp_touch={"level": "AH", "direction": "from_above", "role": "support", "touch_index": 2},
            ma_touch=None,
        )
```

同一個 test 的 body 斷言區塊加一行(放在 `assert body["symbol"] == "2330"` 之後):

```python
        assert body["name"] == "台積電"
```

在檔案最後新增一個測試(驗期貨/查不到名稱那條路):

```python
@pytest.mark.asyncio
async def test_send_signal_name_defaults_to_none_in_body(monkeypatch):
    """未帶 name(期貨 / symbols 快取查不到)→ payload name 為 None。"""
    monkeypatch.setenv("SIGNALS_BOT_PUSH_URL", "http://127.0.0.1:8787/push-signal")
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value = fake_client
    fake_client.__aexit__.return_value = False
    fake_client.post = AsyncMock()
    with patch("services.discord_notifier.httpx.AsyncClient", return_value=fake_client):
        await discord_notifier.send_signal(
            rule_name="t", symbol="MXFF6", price=20000.0, volume=1,
            triggered_at_iso="2026-06-08T05:30:00+00:00",
        )
        body = fake_client.post.call_args.kwargs["json"]
        assert body["name"] is None
```

- [ ] **Step 2:跑測試確認 FAIL**

Run(工作目錄 `…/signal-banner-symbol/backend`):
`C:/side-project/treading-king/backend/.venv/Scripts/python.exe -m pytest tests/test_discord_notifier.py -v`
Expected: `test_send_signal_posts_to_bot_when_url_set` 因 `send_signal()` 收到未知 kwarg `name` 而 **TypeError**(或 `name` 不在 body)而 FAIL;新測試 FAIL。

- [ ] **Step 3:實作 — 加 `name` 參數與 payload 欄位**

`backend/services/discord_notifier.py`,`send_signal` 簽名在 `symbol: str,` 之後加 `name`:

```python
async def send_signal(
    *,
    rule_name: str,
    symbol: str,
    name: str | None = None,
    price: float,
    volume: int,
    triggered_at_iso: str,
    cdp_touch: dict | None = None,
    ma_touch: dict | None = None,
) -> None:
```

payload 加 `"name": name`(放在 `"symbol": symbol,` 之後):

```python
    payload = {
        "symbol": symbol,
        "name": name,
        "rule_name": rule_name,
        "price": price,
        "volume": volume,
        "triggered_at": triggered_at_iso,
        "cdp_touch": cdp_touch,
        "ma_touch": ma_touch,
    }
```

- [ ] **Step 4:跑測試確認 PASS**

Run: `C:/side-project/treading-king/backend/.venv/Scripts/python.exe -m pytest tests/test_discord_notifier.py -v`
Expected: 全 PASS(含新測試,共 4 個)。

- [ ] **Step 5:Commit**

```bash
git add backend/services/discord_notifier.py backend/tests/test_discord_notifier.py
git commit -m "feat(signal): send_signal 送股票名稱進 bot payload

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2:後端 `_fanout` 查名稱並帶入 `send_signal`

**Files:**
- Modify: `backend/services/signal_engine.py:675-687`(`_fanout` 的 discord 區塊)
- Test: `backend/tests/test_signal_engine_monitor.py`(兩個 `notify=True` 的 `_fanout` 測試)

> 背景:`_fanout` 已 `from services.local_store import get_local_store`(檔頭 line 15),無需新增 import。名稱查詢放進**既有 try/except 內**,任何失敗一律 swallow,退化成 `name=None`,不影響 WS broadcast / signals_log。

- [ ] **Step 1:改測試 — 兩個 notify=True 測試 monkeypatch `get_local_store`,並斷言 name**

`test_fanout_calls_discord_when_notify_enabled`:在 `monkeypatch.setattr(se, "get_signal_writer", lambda: fake_writer)` 之後加:

```python
    monkeypatch.setattr(
        se, "get_local_store",
        lambda: MagicMock(market=MagicMock(
            get_symbol=lambda s: {"symbol": s, "name": "台積電", "market": "TSE", "is_etf": False})),
    )
```

同一測試末尾,在 `assert sent[0]["symbol"] == "2330"` 之後加:

```python
    assert sent[0]["name"] == "台積電"
```

`test_fanout_continues_when_discord_raises`:在 `monkeypatch.setattr(se, "get_signal_writer", lambda: writer)` 之後加(讓查表確定不打真實 store):

```python
    monkeypatch.setattr(se, "get_local_store",
                        lambda: MagicMock(market=MagicMock(get_symbol=lambda s: None)))
```

(`test_fanout_skips_discord_when_notify_disabled` 不進通知區塊,**不需改**。)

- [ ] **Step 2:跑測試確認 FAIL**

Run: `C:/side-project/treading-king/backend/.venv/Scripts/python.exe -m pytest tests/test_signal_engine_monitor.py -v`
Expected: `test_fanout_calls_discord_when_notify_enabled` 因 `sent[0]["name"]` KeyError(`_fanout` 還沒傳 `name`)而 FAIL。

- [ ] **Step 3:實作 — `_fanout` 查名稱、傳 `name=`**

`backend/services/signal_engine.py`,把 discord 區塊(現 line 675-687)改成:

```python
        # 3. Discord notify(per-rule 開關;失敗 swallowed,不影響上面兩條)
        if active.notify_discord:
            try:
                meta = get_local_store().market.get_symbol(symbol)
                await discord_notifier.send_signal(
                    rule_name=active.name,
                    symbol=symbol,
                    name=meta["name"] if meta else None,
                    price=tick.price,
                    volume=tick.size,
                    triggered_at_iso=data["triggered_at"],
                    cdp_touch=cdp_touch,
                    ma_touch=ma_touch,
                )
            except Exception as e:
                logger.warning("discord notify failed: %s", e)
```

- [ ] **Step 4:跑測試確認 PASS**

Run: `C:/side-project/treading-king/backend/.venv/Scripts/python.exe -m pytest tests/test_signal_engine_monitor.py -v`
Expected: 全 PASS(三個 `_fanout` 測試 + 其餘)。

- [ ] **Step 5:Commit**

```bash
git add backend/services/signal_engine.py backend/tests/test_signal_engine_monitor.py
git commit -m "feat(signal): _fanout 查 symbol 名稱帶入 send_signal

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3:bot — 解析 `name`,橫幅第一行顯示標的

**Files:**
- Modify: `bot/src/signal.ts`(`SignalPayload`、`parseSignalPayload`、`formatBanner`)
- Test: `bot/src/signal.test.ts`

- [ ] **Step 1:寫失敗測試**

在 `signal.test.ts` 的 `describe("parseSignalPayload", …)` 內加:

```typescript
  it("帶 name → 解析出;不帶 name 仍 valid(name undefined)", () => {
    expect(parseSignalPayload({ ...base, name: "台積電" })!.name).toBe("台積電");
    expect(parseSignalPayload({ ...base })!.name).toBeUndefined();
  });
```

在 `describe("formatBanner", …)` 內加:

```typescript
  it("有 name → 第一行含「名稱 代號」", () => {
    const b = formatBanner({ ...base, name: "台積電" });
    expect(b).toContain("台積電 2330");
  });
  it("無 name(期貨/查不到)→ 第一行只含代號,不出現 undefined / 多餘前綴空格", () => {
    const b = formatBanner({ ...base, name: null });
    expect(b).toContain("｜ 2330 ｜");
    expect(b).not.toContain("undefined");
  });
```

- [ ] **Step 2:跑測試確認 FAIL**

Run(工作目錄 `…/signal-banner-symbol/bot`):`npx vitest run src/signal.test.ts`
Expected: 新增 3 個斷言 FAIL(`name` 不存在於型別/回傳;第一行還沒有 `｜ 2330 ｜`)。

- [ ] **Step 3:實作**

`bot/src/signal.ts`:

(a) `SignalPayload` 介面在 `rule_name: string;` 之後加 `name`:

```typescript
export interface SignalPayload {
  symbol: string;
  rule_name: string;
  name?: string | null;
  price: number;
  volume: number;
  triggered_at: string;   // UTC ISO(後端 datetime.isoformat())
  cdp_touch?: TouchMeta | null;
  ma_touch?: TouchMeta | null;
}
```

(b) `parseSignalPayload` 的 return 物件,在 `rule_name: r.rule_name,` 之後加:

```typescript
    name: typeof r.name === "string" ? r.name : undefined,
```

(c) `formatBanner` 第一行插入標的:

```typescript
export function formatBanner(p: SignalPayload): string {
  const target = p.name ? `${p.name} ${p.symbol}` : p.symbol;
  const lines = [`🔔 **${p.rule_name}** 觸發 ｜ ${target} ｜ 觸發價 ${p.price} ｜ ${taipeiTime(p.triggered_at)}`];
  if (p.cdp_touch) lines.push(touchLine("CDP", p.cdp_touch));
  if (p.ma_touch) lines.push(touchLine("MA", p.ma_touch));
  return lines.join("\n");
}
```

- [ ] **Step 4:跑測試確認 PASS**

Run: `npx vitest run src/signal.test.ts`
Expected: 全 PASS。

- [ ] **Step 5:Commit**

```bash
git add bot/src/signal.ts bot/src/signal.test.ts
git commit -m "feat(bot): 訊號橫幅顯示標的(名稱＋代號),查不到名稱退代號

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4:bot — CDP 中軸線觸發顯示「碰 CDP 中軸」

**Files:**
- Modify: `bot/src/signal.ts`(`levelLabel`)
- Test: `bot/src/signal.test.ts`

> 後端送的 `cdp_touch.level` 是小寫(`ah/nh/cdp/nl/al`,見 `signal_engine.py` 的 `field_map`)。用 `level.toLowerCase() === "cdp"` 判斷,同時相容既有測試用的大寫 `"AH"`。

- [ ] **Step 1:寫失敗測試**

在 `describe("formatBanner", …)` 內加:

```typescript
  it("CDP 中軸線(level cdp)→「碰 CDP 中軸」,不再 CDP CDP", () => {
    const b = formatBanner({ ...base, cdp_touch: { level: "cdp", role: "resistance", touch_index: 9 } });
    expect(b).toContain("碰 CDP 中軸");
    expect(b).not.toContain("碰 CDP CDP");
  });
  it("CDP 其他線(level al)不受影響 → 碰 CDP AL", () => {
    const b = formatBanner({ ...base, cdp_touch: { level: "al", role: "support", touch_index: 5 } });
    expect(b).toContain("碰 CDP AL");
  });
```

- [ ] **Step 2:跑測試確認 FAIL**

Run: `npx vitest run src/signal.test.ts`
Expected: 「中軸」案 FAIL(目前 `level "cdp"` → `toUpperCase()` → 顯示 `碰 CDP CDP`)。

- [ ] **Step 3:實作**

`bot/src/signal.ts` 的 `levelLabel` 改成:

```typescript
function levelLabel(kind: "CDP" | "MA", level: string): string {
  if (kind === "MA") return MA_LABEL[level] ?? level.toUpperCase();
  // CDP 系統的中軸線本身代號就是 cdp,大寫會變「碰 CDP CDP」撞字;顯示「中軸」與前端一致
  return level.toLowerCase() === "cdp" ? "中軸" : level.toUpperCase();
}
```

- [ ] **Step 4:跑測試確認 PASS**

Run: `npx vitest run src/signal.test.ts`
Expected: 全 PASS。

- [ ] **Step 5:Commit**

```bash
git add bot/src/signal.ts bot/src/signal.test.ts
git commit -m "fix(bot): CDP 中軸觸發顯示「中軸」,不再「碰 CDP CDP」

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5:全量回歸 + 收尾

**Files:** 無(只跑測試)

- [ ] **Step 1:bot 全測試**

Run(`…/signal-banner-symbol/bot`):`npm test`
Expected: 8 檔全過(原 62 + 新增的 signal 測試)。

- [ ] **Step 2:backend 相關測試**

Run(`…/signal-banner-symbol/backend`):
`C:/side-project/treading-king/backend/.venv/Scripts/python.exe -m pytest tests/test_discord_notifier.py tests/test_signal_engine_monitor.py -v`
Expected: 全 PASS。

- [ ] **Step 3:檢視訊息實際長相(無自動化,人工核對 spec §6)**

確認 `formatBanner` 對以下三種輸入的輸出符合 spec §6:
- `{rule_name:"碰 CDP", name:"台積電", symbol:"2330", price:129, cdp_touch:{level:"al",role:"support",touch_index:5}}` → `🔔 **碰 CDP** 觸發 ｜ 台積電 2330 ｜ 觸發價 129 ｜ …` + `碰 CDP AL（支撐·第5次）`
- 同上但 `cdp_touch.level:"cdp", role:"resistance", touch_index:9` → 第二行 `碰 CDP 中軸（壓力·第9次）`
- 同上但 `name:null` → 第一行 `… ｜ 2330 ｜ …`

- [ ] **Step 4:回報待人工驗證項**

提醒 user:盤中觸發一次測試規則,確認 Discord 收到的橫幅含標的、中軸顯示正確(離線單元已綠,實機推送需盤中)。

---

## Self-Review 對照(spec → task)

| spec 需求 | 對應 |
|---|---|
| 橫幅顯示「名稱 代號」 | Task 3 |
| 查不到名稱退代號 | Task 3(`p.name ? … : p.symbol`)+ Task 2(`name=None`)+ Task 1(payload `null`) |
| fallback 純文字也有名稱 | Task 1+2(name 走 payload,獨立圖卡) |
| `碰 CDP CDP` → `碰 CDP 中軸` | Task 4 |
| 其他 CDP 線不變 | Task 4 第二個測試 |
| MA 行不動 | 無 task 改動 MA 分支(`levelLabel` 的 MA 路徑原樣) |
| 名稱來源便宜 | Task 2(`market.get_symbol` 記憶體查表) |

非目標(MA 正名、線別中文、其他 payload 欄位)無對應 task —— 刻意不做。
