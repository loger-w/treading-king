# Discord Bot 分時推播微調 + 訊號推播啟用 — 設計文件

**日期**:2026-06-05
**範圍**:Discord bot 分時回覆(`bot/`)+ 共用畫圖層(`frontend/src/lib/intraday-chart-svg.tsx`)+ 訊號推播啟用(後端程式碼已就緒,本案為設定/驗證 + 第二階段設計)
**狀態**:設計完成,brainstorm 4 個決策已由 user 拍板
**相關**:[個股查詢 bot design](./2026-06-05-discord-stock-bot-design.md)、[訊號 Discord 通知 design](./2026-05-26-monitor-list-and-discord-notify-design.md)

---

## 1. 目的

針對 `p代號` 查詢 bot 的分時回覆做三個外觀微調,並把「訊號觸發推 Discord」這條既有但未啟用的鏈路正式接通。四個需求對應 user 在 brainstorm 拍板的四個決策。

---

## 2. 決策(user 拍板)

| # | 需求 | 決策 |
|---|---|---|
| 1 | 分時圖要不要顯示 MA5/MA20 | **只移圖上的兩條線**;embed 下方「均線」文字欄位**保留** |
| 2 | CDP 加 `*` | **圖 + embed 文字都加** —— 標出 5 條 CDP 線裡正中央的「樞紐」那條 |
| 3 | 五檔呈現 | 改成**左右掛單文字**(買盤在左 / 賣盤在右,最佳價在最上) |
| 4 | 訊號推播 | **分段**:第一階段先純文字 webhook(設定 + 盤中實測);第二階段再附分時圖(本案只設計、不實作) |

---

## 3. 逐功能設計

### 3.1 功能 1 — 分時圖移除 MA5/MA20 線(embed 文字保留)

**檔案**:`bot/src/reply.ts`

`loadSlow()` 內組 chart flags 時把 `ma` 關掉:

```diff
- const flags = { vwap: true, cdp: true, camarilla: false, volume: true, ma: true };
+ // bot 推播的分時圖不畫 MA5/MA20 兩條水平線(embed 的「均線」文字欄位仍保留,走 s.ma)
+ const flags = { vwap: true, cdp: true, camarilla: false, volume: true, ma: false };
```

**為什麼 embed 文字會自動留著**:embed 的「均線」欄位由 `buildReply({ ma: s.ma })` 產生(`composeReply` → `buildReply`),吃的是 `s.ma` 資料、**不吃 chart flags**。所以關 `flags.ma` 只影響 PNG 圖層,文字欄位不動。bot-only,不影響網頁。

**測試**(`bot/src/reply.test.ts`):新增一條 —— `loadSlow` 呼叫 `render` 時,傳入的 `flags.ma === false`(且 `vwap/cdp/volume` 仍為 true)。捕捉 `render` 收到的 input 來斷言,encode「推播分時圖不畫 MA 線」這個意圖。

### 3.2 功能 2 — CDP 樞紐線加 `*`(圖 + embed)

**圖**:`frontend/src/lib/intraday-chart-svg.tsx`(網頁與 bot 共用同一份)

`computeIntradayGeometry()` 內組 CDP label 時,只有中央樞紐(`k === "cdp"`)的價格標籤後綴 `*`:

```diff
  for (const k of visibleCdpKeys) {
    labelInputs.push({
      originalY: scaleY(cdp[k]),
-     text: formatTickPrice(cdp[k]),
+     // 中央 CDP 樞紐標 *,在 5 條同色(accent)CDP 線裡標出真正的樞紐
+     text: k === "cdp" ? `${formatTickPrice(cdp[k])}*` : formatTickPrice(cdp[k]),
      color: theme.accent,
    });
  }
```

> ⚠️ **共用層副作用(已向 user 揭露並同意)**:此圖層網頁分時圖也用,所以網頁的 CDP 樞紐標籤也會多一個 `*`。視為順手標出真樞紐、無害。若日後要 bot-only,再加 prop 隔開。

**embed 文字**:`bot/src/embed.ts`

```diff
  const cdp = args.cdp
-   ? `AH ${args.cdp.ah} ／ NH ${args.cdp.nh} ／ CDP ${args.cdp.cdp} ／ NL ${args.cdp.nl} ／ AL ${args.cdp.al}`
+   ? `AH ${args.cdp.ah} ／ NH ${args.cdp.nh} ／ CDP* ${args.cdp.cdp} ／ NL ${args.cdp.nl} ／ AL ${args.cdp.al}`
    : "—";
```

**測試**:
- `frontend/src/lib/intraday-chart-svg.test.ts`:新增斷言 —— 當 `cdp.cdp` 在可見範圍內,`resolvedLabels` 中存在 `text === formatTickPrice(cdp.cdp) + "*"`;且非樞紐的其他 CDP 線不帶 `*`。既有 snapshot 測試會因 SVG 改變而更新(`vitest -u`)。
- `bot/src/embed.test.ts`:新增/擴充 —— `buildReply` 的 CDP 欄位 value 含 `"CDP*"`。

### 3.3 功能 3 — 五檔改左右掛單文字

**檔案**:`bot/src/embed.ts`(`formatLadder`),bot-only

**現狀**:垂直階梯(賣5→賣1 / 分隔線 / 買1→買5),單欄。

**新版**:兩欄並排,買盤在左、賣盤在右,**最佳價在最上**(買1/賣1 在第一列,往下到第5檔):

```
   買盤          賣盤
634.50  340 │ 636.00  120
634.00  210 │ 636.50   88
633.50   88 │ 637.00   60
   —     —  │   —      —
   —     —  │   —      —
```

規則沿用既有 helper:
- `cell(price)`:`price===0`(鎖漲跌停的市價單)顯示「市價」,否則 `toFixed(2)`,右對齊。
- `qty(size)`:`size>0` 顯示張數,否則「—」,右對齊。
- 不足 5 檔補「—  —」。
- 第一列為表頭「買盤 / 賣盤」。

`buildReply` 仍把 `formatLadder(...)` 包進 ```` ``` ```` code block,維持等寬對齊。鎖漲跌停的「🔺鎖漲停 / 🔻鎖跌停」標示不變。

**測試**(`bot/src/embed.test.ts`,改寫既有 `formatLadder` 測試):
- 表頭含「買盤」「賣盤」。
- 第一列同時含買1、賣1 的價(最佳價在最上)。
- 每列以 `│` 分隔買賣兩側。
- `price=0` → 該格顯示「市價」。
- 缺檔 → 補「—」。
- `buildReply` 降級測試:原本斷言 description 含 `"買1"` 的兩條,改為斷言含 `"買盤"`(代表五檔區仍 render),語意不變(五檔在/不在)。

### 3.4 功能 4(第一階段)— 訊號純文字推播:設定 + 驗證

**現狀(已實作,非本案新寫)**:
- `backend/services/signal_engine.py` `_fanout()` 在 `active.notify_discord` 為真時呼叫 `discord_notifier.send_signal(...)`(失敗 swallow,不影響 WS broadcast + 歷史寫入)。
- `backend/services/discord_notifier.py` 已完整:webhook 未設 → no-op;設了 → POST 純文字 embed(代號/價/量/CDP/MA);httpx 例外 swallow。
- `backend/.env.example` 已有 `SIGNALS_DISCORD_WEBHOOK_URL`(line 25)。
- 測試已涵蓋:`test_discord_notifier.py`(3 條)+ `test_signal_engine_monitor.py`(`_fanout` 開/關/raise 3 條)。

**本案要做**:
1. 跑 `pytest` 確認上述測試綠燈(回歸保護)。
2. 文件化啟用步驟,標成 **user 待辦**(見 §6)—— 程式不缺、缺設定與盤中實測。

**本案不寫新後端程式碼**(功能 4 第一階段是「啟用既有鏈路」)。

---

## 4. 功能 4 第二階段(附分時圖)— 設計,不實作

目標:訊號觸發時,推播長得跟 `p代號` 查詢一樣(分時圖 PNG + 左右掛單 + CDP*/均線 embed),而非純文字。

**架構選擇:後端訊號 → bot 內部 HTTP → bot 產圖 + 貼文**(推薦)

理由:產圖只能在 Node 端(`react-dom/server` + `resvg`);bot 已有 `loadSlow` + `composeReply` 整套。讓 bot 多開一個內部 endpoint,後端訊號改打 bot(而非 webhook),即可重用全部既有產圖/排版邏輯,且自動套上功能 1/2/3 的調整。

- **bot**(`bot/`):
  - 新增極小 HTTP listener(`node:http`),`POST /notify { symbol, rule_name, trigger_price, ... }`(綁 `127.0.0.1`,僅本機)。
  - 收到後 `loadSlow(symbol)` → 組一個帶「規則名」抬頭的 reply(重用 `composeReply`,前面加一行 `🔔 {rule_name} 觸發`)→ 經 `client.channels.fetch(SIGNAL_CHANNEL_ID).send(...)` 貼到指定頻道。
  - 新 config:`SIGNAL_CHANNEL_ID`、`NOTIFY_HTTP_PORT`。
- **後端**(`backend/services/discord_notifier.py`):
  - 新增「打 bot」模式:若 `BOT_NOTIFY_URL` 有設,改 POST 到 bot 的 `/notify`(取代 webhook);否則維持現有 webhook 純文字。兩種都 swallow 失敗。
  - 新 env:`BOT_NOTIFY_URL`(如 `http://127.0.0.1:{port}/notify`)。

**第二階段測試**:bot `/notify` handler 純函式部分(組 payload / 找頻道)可注入假 client 測;後端 `send_signal` 在 `BOT_NOTIFY_URL` 設/未設兩路徑各一測。

> 第二階段需要的盤中實測同樣依賴交易時段 + bot 上線 + token,故與第一階段一起在盤中驗。

---

## 5. 錯誤處理 / 降級(不變)

| 場景 | 處理 |
|---|---|
| 產圖失敗 | `safeRender` 回 null → 純文字 embed(現價/五檔/CDP/MA 仍在),既有行為 |
| 五檔抓不到 | `quote=null` → 五檔區顯示「五檔暫無資料」,不拖垮圖/CDP/MA |
| webhook 未設 | `send_signal` 直接 return,no-op |
| webhook / bot 推送失敗 | httpx 例外 swallow + log,不影響 WS broadcast + 歷史寫入 |

---

## 6. 待辦(需要 user,無法由 agent 完成)

1. **設定 webhook**:把 Discord webhook URL 填進 `backend/.env` 的 `SIGNALS_DISCORD_WEBHOOK_URL`(secret,agent 不代填),重啟 backend。
2. **盤中實測**(台股 9:00–13:30):建一條必觸發規則(如 `close > 0`)、`notify_discord` 打開、目標股加進監聽,確認三件事:前端觸發歷史 +1、本機 `signals_log` +1、Discord 收到 embed。收盤後只能等下一個交易時段。
3. (第二階段才需)決定訊號要貼哪個頻道 → `SIGNAL_CHANNEL_ID`。

---

## 7. 測試計畫總表

| 層 | 檔案 | 重點 |
|---|---|---|
| bot 單元 | `bot/src/reply.test.ts` | 新:render 收到 `flags.ma===false` |
| bot 單元 | `bot/src/embed.test.ts` | 改:左右掛單格式;新:CDP 欄位含 `CDP*` |
| 前端單元 | `frontend/src/lib/intraday-chart-svg.test.ts` | 新:CDP 樞紐 label 帶 `*`;更新 snapshot |
| 後端單元 | `test_discord_notifier.py` / `test_signal_engine_monitor.py` | 跑既有,回歸保護 |
| 視覺 | 手動 render 一張 PNG | 確認無 MA 線、CDP* 在、左右掛單排版 |
| E2E | 盤中(user) | webhook 真的收到(§6) |

---

## 8. 不在本案 scope

- 功能 4 第二階段的**實作**(只設計)。
- LINE 通知、通知 throttle、per-rule webhook —— 沿用既有 design 的後續清單。
- 五檔深度長條圖(user 選了左右掛單文字,非長條圖)。
