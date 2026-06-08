# 訊號觸發 → 走 Discord bot 推完整圖卡(設計)

- 日期:2026-06-08
- 狀態:設計定案,待寫實作計畫
- 相關:`docs/superpowers/specs/2026-05-26-monitor-list-and-discord-notify-design.md`(訊號通知原始設計)、`2026-06-05-discord-bot-tweaks-design.md`(bot 三則圖卡)

---

## 1. 背景 / 現況

目前有**兩條互不相干**的 Discord 路徑:

1. **訊號推播(Python,純文字 webhook)**
   - `signal_engine._fanout()` 規則觸發 → `discord_notifier.send_signal()` → 組純文字 embed POST 到 `SIGNALS_DISCORD_WEBHOOK_URL`。
   - 有 per-rule `notify_discord` 開關;webhook URL 目前沒設(空 → no-op);**沒有圖**。

2. **互動 bot(Node `bot/`,會渲染圖)**
   - 收到 `p代號` → 打後端 REST → resvg 把前端圖元件渲成 PNG → 回**三則**(文字卡 + 分時圖 + 五檔圖)。
   - 只被動回訊息,**沒有主動推送、沒有對內 HTTP 入口**。

**缺口:** 訊號在 Python 後端觸發,但會渲染圖卡的能力全在 Node bot 裡,兩條沒有交集。

## 2. 目標 / 成功標準

規則在後端觸發、且該規則 `notify_discord=true` 時,Node bot 在指定頻道推出跟 `p代號` 同款的**三則圖卡**(文字卡 + 分時圖 + 五檔圖),最上面疊一條「哪條規則、碰到哪個位階、觸發價/時間」的**觸發橫幅**。

成功標準:
- 盤中觸發一條測試規則 → 指定 Discord 頻道在數秒內收到「橫幅 + 文字卡 + 分時圖 + 五檔圖」。
- bot 沒開 / 渲圖失敗 / 頻道未設 → 後端與 bot 都不 crash,訊號照常寫進 `active_signals` / `signals_log`。
- `p代號` 互動查詢行為完全不變。

## 3. 架構總覽(純 push,localhost IPC)

```
signal_engine._fanout (規則觸發, per-rule notify_discord 開關)
        │  已有 3 件事:① WS broadcast ② 寫 signals_log ③ discord_notifier.send_signal
        ▼
discord_notifier.send_signal   ← 改寫:不再打 webhook,改 POST 給 bot
        │  POST http://127.0.0.1:8787/push-signal
        │  {symbol, rule_name, price, volume, triggered_at, cdp_touch?, ma_touch?}
        ▼
bot push-server (只 bind 127.0.0.1, 只收 application/json)   ← 新增
        │  立刻回 202(不擋後端 tick loop)→ 背景:
        ▼
buildSymbolMessages(symbol) → 注入觸發橫幅 → channel.send() 三則
   重用 loadSlow / getQuote / renderChartPng / renderQuotePng / composeReply(全部現成)
```

**核心洞見:** `signal_engine.py` **零改動**。只要 `discord_notifier.send_signal()` 保持同樣簽名,把函式內容從「打 webhook」換成「POST 給 bot」即可;per-rule `notify_discord` 開關、call site 都不變。

**為什麼用 HTTP:** 不是為了對外,純粹是後端 Python、bot Node,兩個行程要溝通只能走 IPC,localhost HTTP 是最簡單的 IPC。

## 4. 後端改動(小)

- `backend/services/discord_notifier.py`
  - `send_signal(...)` **簽名不變**(`rule_name, symbol, price, volume, triggered_at_iso, cdp_touch, ma_touch`)。
  - body 改成:POST `{symbol, rule_name, price, volume, triggered_at, cdp_touch, ma_touch}` 到 `SIGNALS_BOT_PUSH_URL`。
  - URL 留空 → no-op(跟現在 webhook 留空一樣);`httpx` timeout 3s;失敗 `logger.warning` + swallow。
  - 移除 `SIGNALS_DISCORD_WEBHOOK_URL` 的讀取(webhook 退役)。
- `backend/services/signal_engine.py`:**零改動**。
- env:`backend/.env.example` 加 `SIGNALS_BOT_PUSH_URL=http://127.0.0.1:8787/push-signal`,移除 `SIGNALS_DISCORD_WEBHOOK_URL`。

## 5. bot 改動

### 新檔
- `bot/src/push-server.ts`
  - Node 內建 `http.createServer`,**只 `listen(PORT, "127.0.0.1")`**(機外打不到)。
  - 只接受 `POST /push-signal` 且 `Content-Type: application/json`;其餘回 404/405/415。
  - 流程:讀 body → `parseSignalPayload` → **立刻回 `202`** → 背景 `handleSignalPush(payload)`。
  - 在 `Events.ClientReady` 之後才 `startPushServer(client)`(server 在 bot 行程內、登入後啟動,這樣 `client` 能 fetch/send 頻道);啟動前極短窗口到達的 POST 視同 bot 未開(connection refused,後端 swallow)。
- `bot/src/signal.ts`
  - `SignalPayload` 型別 + `parseSignalPayload(raw): SignalPayload | null`(驗證必填欄位,壞的回 null → 400)。
  - `formatBanner(payload): string`(見 §6)。
  - `handleSignalPush(payload, deps)`:解析目標頻道 → 空/抓不到 → log + return;否則 `buildSymbolMessages(symbol)` → 把橫幅注入第一則 → 依序 `channel.send()`。deps 注入 `fetchChannel` / `buildSymbolMessages` 供測試。

### 小重構(為共用,非順手改別處)
- `bot/src/messages.ts`(新):把 `index.ts` `handle()` 內「產三則訊息」的邏輯抽成 `buildSymbolMessages(symbol)`(共用 `slow` 30s 快取 + `getQuote` + `renderQuotePng` + `composeReply`),讓**訊息處理器**與 **push handler** 共用。共用的 `slow` 快取一併搬來這裡。
- `bot/src/index.ts`:`handle()` 改成呼叫 `buildSymbolMessages`,變薄;`ClientReady` 時呼叫 `startPushServer(client)`。

### 橫幅注入
- 橫幅是一段 `content` 字串,疊在第一則訊息上方:`messages[0] = { ...messages[0], content: banner + (既有 content 則換行接上) }`。`buildReply` 回的是 `{embeds:[...]}`(無 content),加 content 安全;空盤資料那則本身是 `{content}`,則接在前面。

### env
- `bot/.env.example` 加:
  - `SIGNALS_DISCORD_CHANNEL_ID=`(留空 → 略過推播 + log)
  - `BOT_PUSH_PORT=8787`
- 使用者本機 `bot/.env`(gitignored)填 `SIGNALS_DISCORD_CHANNEL_ID=1487309833630908448`。

## 6. 觸發橫幅格式

用 `cdp_touch` / `ma_touch` 的實際欄位:`{level, direction, role, touch_index}`,`role` ∈ support/resistance/touch、`touch_index` = 當日第幾次觸碰。

- 第一行:`🔔 **{rule_name}** 觸發 ｜ 觸發價 {price} ｜ {台北時間 HH:mm:ss}`
  - 時間:`triggered_at`(UTC ISO)用 `Intl.DateTimeFormat('zh-TW', { timeZone:'Asia/Taipei', hour/minute/second:'2-digit' })` 轉,host 時區無關。
- 若有 `cdp_touch`:`碰 CDP {level}（{role 中譯}·第{touch_index}次）`
- 若有 `ma_touch`:`碰 MA {level}（{role 中譯}·第{touch_index}次）`
- role 中譯:support→支撐、resistance→壓力、touch→觸碰。
- `level` 顯示對齊文字卡標籤:CDP 為 AH/NH/CDP/NL/AL;MA 後端回的是內部欄位名 `sma_5`/`sma_20`,bot 端對應成 `MA5`/`MA20`(若 CDP 也回小寫欄位名則一併對應大寫)。
- cdp/ma 兩行只在對應欄位存在時出現。

文字卡(`buildReply` embed)本身已含現價/開高低/均價量/CDP/均線,橫幅只補「what triggered」,不重複。

## 7. 資料流(逐步)

1. tick 進來,`signal_engine` 判定某 `active` 規則觸發。
2. `_fanout`:WS broadcast + 寫 `signals_log` + (若 `notify_discord`)呼叫 `discord_notifier.send_signal(...)`。
3. `send_signal` POST payload 給 bot,得到 202 後立即返回(localhost,毫秒級)。
4. bot push-server 收到 → 驗證 → 回 202 → 背景:解析 `SIGNALS_DISCORD_CHANNEL_ID` → `client.channels.fetch(id)`。
5. `buildSymbolMessages(symbol)`:`slow.get` 抓分時 K/CDP/MA + 渲分時圖、即時 `getQuote` + 渲五檔圖、`composeReply` 組三則。
6. 把橫幅注入第一則 → 依序 `channel.send()`。

## 8. 失敗與降級(全 best-effort;訊號本身一律已存 `active_signals`/`signals_log`)

| 情況 | 行為 |
|---|---|
| bot 沒開 | 後端 POST connection refused(localhost 很快)→ swallow + log,同現行 webhook |
| 渲圖失敗(resvg / 五檔) | `composeReply` 既有降級:跳過該張圖,文字卡 + 橫幅照送 |
| 頻道未設 / 抓不到 | bot log 一行,不送、不 crash |
| payload 壞 | push-server 回 400,不送 |

bot 先回 202 再背景渲染 → 後端 tick loop 不被渲圖耗時卡住。

## 9. 安全(localhost-only 的理由與前提)

- push-server 只 bind `127.0.0.1` → 區網/外網別台機器打不到,遠端攻擊面 = 0。
- 同機其他程式理論上能 POST 叫 bot 發訊息,但這是個人單機,風險 ~0。
- 瀏覽器 CSRF:入口**只收 `application/json`** → 跨來源 JSON POST 觸發 CORS preflight,server 不回應即被擋;就算被打到,最慘只是「自己頻道被丟一張股票圖」(無資料外洩、無破壞操作)。
- 故 **v1 不加 shared secret**,localhost bind + 只收 JSON 即足夠。
- **前提(背後的問題):** 上述免驗證是建立在「永遠只在本機」之上。哪天後端/bot 上伺服器或在多人共用機器跑,這個開放入口要回頭補(bind 位址 / secret)。入口已獨立成 `push-server.ts`,屆時好加。

## 10. 設定總表(兩邊 `.env` + `.env.example` 都更新)

| 端 | 變數 | 預設 | 說明 |
|---|---|---|---|
| backend | `SIGNALS_BOT_PUSH_URL` | `http://127.0.0.1:8787/push-signal` | 空 = 不推 |
| backend | ~~`SIGNALS_DISCORD_WEBHOOK_URL`~~ | — | 退役、移除 |
| bot | `SIGNALS_DISCORD_CHANNEL_ID` | (空) | 空 = 略過;本機填 `1487309833630908448` |
| bot | `BOT_PUSH_PORT` | `8787` | push-server 埠 |

`start.ps1` 不用改(bot 已隨 `npm run start` 起,http server 在 bot 行程內)。

## 11. 測試(對齊現有 dep-injection 風格)

- **bot vitest**
  - `formatBanner`:有/無 cdp_touch、有/無 ma_touch、role 中譯、touch_index、時間格式。
  - `parseSignalPayload`:缺必填 → null;完整 → 正確物件。
  - `handleSignalPush`(注入假 deps):頻道空 → 不呼叫 send;valid → send 被呼叫且第一則含橫幅;渲圖回 null → 仍送文字卡(降級)。
  - push-server HTTP 殼:非 POST/非 JSON/壞 body → 對應狀態碼(輕量,用 Node http 起臨時 server 打一發)。
- **backend pytest**
  - `send_signal`:URL 設定時 POST 正確 JSON 到 bot URL;URL 空 → 不發;httpx 拋錯 → swallow 不外溢。
- **signal_engine**:不動,無需新測試。

## 12. 非目標(留 v2)

- 每條規則指定不同頻道(需改 rule schema + 前端 UI)。
- 前端設定頁設頻道 / 開關。
- webhook 後援(本案純 push,不做雙寫)。
- 多訊號合批 / 推播節流(現有 per-rule `cooldown_seconds` 已是天然去重)。

## 13. 已定決策摘要

- 傳輸:**純 push**(backend → bot localhost HTTP),非 poll、非 webhook 後援。
- 內容:**完整三則圖卡 + 觸發橫幅**。
- 頻道:env `SIGNALS_DISCORD_CHANNEL_ID`,本機值 `1487309833630908448`;空 → no-op。
- 安全:**v1 不加 secret**,localhost bind + 只收 JSON;前提是維持本機。
- webhook:`SIGNALS_DISCORD_WEBHOOK_URL` 退役。
