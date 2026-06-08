# 訊號觸發橫幅:加上標的(代號＋名稱)＋ CDP 中軸正名(設計)

- 日期:2026-06-08
- 狀態:設計定案,待寫實作計畫
- 相關:`2026-06-08-signal-push-via-bot-design.md`(§6 橫幅格式即本案要改的對象)、`2026-06-05-cdp-limitup-open-and-breakout-retest-design.md`(CDP 策略)
- 分支:`fix/signal-banner-symbol`(off main)

---

## 1. 背景 / 現況

訊號觸發時 bot 在第一則訊息上方疊一條「觸發橫幅」(`bot/src/signal.ts` 的 `formatBanner` + `touchLine`)。目前長相:

```
🔔 碰 CDP 觸發 ｜ 觸發價 129 ｜ 12:24:01
碰 CDP AL（支撐·第5次）
```

使用者回報三個點:

1. **看不出是哪一檔。** 橫幅第一行只有「規則名 / 觸發價 / 時間」,**完全沒有股票代號或名稱**。下方圖卡(embed)標題雖有「名稱 代號」,但 (a) 橫幅本身無法一眼掃到、(b) 圖卡抓不到資料退純文字時(PR #16 fallback)連名稱都沒有。
2. **「碰 CDP CDP」看起來像壞掉。** 第二行格式是 `碰 {系統} {碰到的線}`;CDP 系統的 5 條線本身有一條就叫 `CDP`(正中間的中軸),碰到它時系統名與線名撞字 → `碰 CDP CDP`。不是 bug,但易誤會,且**前端網頁早已把中軸顯示成「CDP 中軸」**(`frontend/src/lib/signal-format.ts` 的 `LEVEL_ZH.cdp = "CDP 中軸"`),只有 bot 這條橫幅仍用裸大寫 → 兩邊不一致。
3. **線別代號 AH/NH/CDP/NL/AL 不知道是什麼意思。** 純說明需求,見附錄 A。

## 2. 目標 / 成功標準

- 觸發橫幅第一行顯示**標的「名稱 代號」**(查不到名稱時退化成只有代號)。
- 圖卡抓不到資料退純文字時,橫幅**一樣**有標的(因為走 payload,不依賴圖卡那次抓取)。
- 碰到中軸線時顯示**「碰 CDP 中軸」**,與前端一致;其餘線別(AH/NH/NL/AL)顯示不變。
- `p代號` 互動查詢、其餘訊號行為完全不變。

## 3. 已定決策(brainstorming 結論)

| 議題 | 決策 | 理由 |
|---|---|---|
| 標的顯示 | **代號＋名稱**(`台積電 2330`) | 最清楚;且要在「圖卡 fail 退純文字」時也看得到 → 名稱**必須進後端 payload**,不能只在 bot 端串接 |
| 中軸命名 | **`碰 CDP CDP` → `碰 CDP 中軸`** | 與前端 `signal-format.ts` 一致、不再撞字 |
| 線別中文 | **維持代號**(AH/NH/CDP/NL/AL) | 方向已有「壓力/支撐」提示,加全名會把每行變長;意義改放文件附錄 |

## 4. 為何名稱要走後端 payload(而非 bot 端取)

bot 的 `buildSymbolMessages(symbol)` 雖然會抓到名稱(圖卡 embed 標題用),但那是**圖卡渲染路徑**的產物;當該路徑失敗(後端重啟/斷線)時,`handleSignalPush` 走 fallback 純文字橫幅,此時 bot 端**沒有**名稱。決策 1 要求 fallback 也要有標的 → 名稱必須由後端隨 payload 帶來,獨立於圖卡抓取成敗。後端取名稱很便宜:`get_local_store().market.get_symbol(symbol)` 是記憶體內 O(1) 查表(回 `{symbol, name, market, is_etf}` 或 `None`),零 I/O。

## 5. 改動(共 4 檔:2 後端 / 2 bot)

### 後端

- **`backend/services/signal_engine.py`** — `_fanout()` 內、呼叫 `send_signal` 之前查名稱:
  - `from services.local_store import get_local_store`(已是專案慣例)。
  - `meta = get_local_store().market.get_symbol(symbol)`;`name = meta["name"] if meta else None`。
  - 查名稱與 `send_signal` 一起放進**既有的 `try/except`**(失敗一律 swallow,不影響 WS broadcast / signals_log 兩條)。
  - `send_signal(..., name=name)`。
- **`backend/services/discord_notifier.py`** — `send_signal`:
  - 簽名多收 `name: str | None = None`(放在既有具名參數後)。
  - payload 加一欄 `"name": name`。其餘不變。

### bot

- **`bot/src/signal.ts`**:
  - `SignalPayload` 介面加 `name?: string | null`。
  - `parseSignalPayload`:解析 `name`(選填字串;缺/非字串 → `undefined`,**不影響必填驗證**,payload 仍 valid)。
  - `formatBanner`:第一行插入標的段。
    - `const target = p.name ? `${p.name} ${p.symbol}` : p.symbol;`
    - 新第一行:`` `🔔 **${p.rule_name}** 觸發 ｜ ${target} ｜ 觸發價 ${p.price} ｜ ${taipeiTime(p.triggered_at)}` ``
  - `levelLabel`(CDP 分支):`level === "cdp"` → `"中軸"`,其餘維持 `level.toUpperCase()`。MA 分支不動。

### 不動

- `bot/src/push-server.ts` / `messages.ts` / `index.ts`:零改動(payload 多一欄選填,既有解析相容)。
- `signal_engine` 的 WS broadcast / signals_log:零改動。
- `.env` / `.env.example`:零改動(沒有新設定)。

## 6. 訊息格式 before → after

```
─ 現在 ─────────────────────────────────────
🔔 碰 CDP 觸發 ｜ 觸發價 129 ｜ 12:24:01
碰 CDP AL（支撐·第5次）
🔔 碰 CDP 觸發 ｜ 觸發價 286 ｜ 12:08:53
碰 CDP CDP（壓力·第9次）          ← 撞字

─ 改完 ─────────────────────────────────────
🔔 碰 CDP 觸發 ｜ 台積電 2330 ｜ 觸發價 129 ｜ 12:24:01
碰 CDP AL（支撐·第5次）
🔔 碰 CDP 觸發 ｜ 群創 3481 ｜ 觸發價 286 ｜ 12:08:53
碰 CDP 中軸（壓力·第9次）          ← 正名

─ 查不到名稱(如期貨 MXF / 不在 symbols 快取) ─
🔔 碰 CDP 觸發 ｜ 2330 ｜ 觸發價 129 ｜ 12:24:01   ← 只剩代號,不空字串、不壞
```

(標的位置:接在「觸發」之後、「觸發價」之前,與 brainstorming 核可的預覽一致。)

## 7. 邊界與降級

| 情況 | 行為 |
|---|---|
| symbols 快取查不到該檔(期貨、快取未載) | `name=None` → 橫幅只顯示代號 |
| 名稱查詢拋例外 | 在既有 `try/except` 內 → swallow,訊號其餘路徑不受影響(最差退成無名稱) |
| 圖卡抓不到退純文字 | 橫幅仍含「名稱 代號」(name 在 payload) |
| 舊 bot / 舊後端 混搭 | payload `name` 選填:新 bot 收舊 payload(無 name)→ 只顯示代號;舊 bot 收新 payload → 忽略多餘欄位 |

## 8. 測試(沿用既有 dep-injection / vitest / pytest 風格)

- **bot `signal.test.ts`**
  - `formatBanner`:有 `name` → 含「名稱 代號」;**`name` 缺失 → 只含代號**(守期貨/查不到那條路)。
  - `touchLine`(或經 `formatBanner`):`level:"cdp"` → `碰 CDP 中軸`;`level:"al"` → `碰 CDP AL`(確認只動中軸、沒誤傷其他線)。
  - `parseSignalPayload`:帶 `name` → 解析出;不帶 `name` 但其餘必填齊全 → 仍 valid(name 為 undefined)。
- **backend `test_discord_notifier.py`**
  - `send_signal(name=...)`:POST 的 JSON body 含 `name` 欄位且值正確;`name=None` → body `name` 為 `null`。
- **`signal_engine`**:名稱查詢屬 `_fanout` 細節;若既有 `_fanout` 測試會驗 `send_signal` 呼叫參數,補上 `name`。否則不新增(查表為純記憶體、低風險)。

每個測試驗的是**意圖**(fallback 要退代號、中軸要正名、name 選填不破壞驗證),非只比字串。

## 9. 非目標

- MA 觸發行(`碰 MA MA5`)維持不變 —— 使用者只反映 CDP,且 MA/MA5 非同字、誤會度低。
- 線別代號附中文全名(決策為「維持代號」)。
- 後端 payload 其他擴充(market / is_etf 等)—— 本案只加 `name`。
- 名稱顯示樣式(粗體 / 換行 / 位置)再調整 —— 先照核可預覽。

## 附錄 A — CDP 五線中文對照(回答使用者提問)

CDP 是**逆勢操作指標**,用「昨日」高 H / 低 L / 收 C 算出「今日」5 個固定價位帶(公式見 `backend/services/cdp.py`),由高到低:

| 代號 | 中文 | 角色 | 公式 |
|---|---|---|---|
| **AH** | 最高值 | 最上緣,最強壓力(站上=強勢突破) | `CDP + (H − L)` |
| **NH** | 近高值 | 上方壓力 | `2·CDP − L` |
| **CDP** | 中心值(中軸) | 正中間,多空分界 | `(H + L + 2C) / 4` |
| **NL** | 近低值 | 下方支撐 | `2·CDP − H` |
| **AL** | 最低值 | 最下緣,最強支撐(跌破=弱勢) | `CDP − (H − L)` |

口訣:上半 `AH/NH` 壓力區、下半 `NL/AL` 支撐區、`CDP` 居中分界。橫幅括號內的「壓力/支撐」是引擎判斷這筆觸碰把該線當壓力或支撐測試(看價格從上或下接近),與線別代號是兩件事。
