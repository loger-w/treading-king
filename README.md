# trading-king — 個人台股即時監控

整合富邦 Neo API 的本地版股票篩股 + 即時訊號工具。所有人在自己 Windows 電腦本機跑、共用同一個 Supabase 資料庫，靠 `.env` 的 `USER_LABEL` 隔離各自的自選清單 / 策略 / 訊號紀錄。

## 你需要先準備

- **作業系統**：Windows 10/11 x64（富邦 SDK 是 win_amd64 wheel）
- **富邦證券帳戶 + TradeAPI key**：到 https://www.fbs.com.tw/TradeAPI/docs/key-management 申請
- **Python 3.12**：到 https://www.python.org/downloads/ 下載，安裝時勾「Add Python to PATH」
- **Node.js 20+**：https://nodejs.org/
- **Git**：https://git-scm.com/download/win
- **Supabase service_role key**：私訊 `loger` 索取（**勿外流**，等同 admin 權限）
- **你自己選的 USER_LABEL**：2~20 字、`[a-z0-9_-]`。先在群裡喊一聲避免撞名（例如 `frank`、`bobo`）

## 安裝

1. clone 專案

```powershell
git clone https://github.com/<your-user>/trading-king.git C:\trading-king
cd C:\trading-king
```

2. 下載富邦 SDK wheel

到 https://www.fbs.com.tw/TradeAPI/docs/welcome 登入後找「下載 SDK」，下載最新 Windows x64 wheel（檔名類似 `fubon_neo-2.2.8-cp37-abi3-win_amd64.whl`），放到 `backend\wheels\` 目錄。

3. 設定 backend 環境變數

```powershell
Copy-Item backend\.env.example backend\.env
notepad backend\.env
```

至少要填：
- `FUBON_PERSONAL_ID` / `FUBON_API_KEY`（你的富邦帳號）
- `SUPABASE_URL` / `SUPABASE_KEY`（loger 給你的 URL + service_role key）
- `BFF_API_KEY`：隨便填一個秘密字串（前後端共用，例如 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生一個）
- `USER_LABEL`：你的 label（例如 `frank`）
- `CACHE_JOB_OWNER`：**留空**（只有 loger 會跑 cache job）

4. 設定 frontend 環境變數

```powershell
Copy-Item frontend\.env.example frontend\.env
notepad frontend\.env
```

填 `VITE_BFF_API_KEY=<跟 backend\.env 一樣那個>`。

5. 一鍵安裝

```powershell
.\install.ps1
```

預計跑 5~10 分鐘（pip install + npm install）。

## 啟動

```powershell
.\start.ps1
```

會開兩個 PowerShell 視窗（backend + frontend）。等 backend log 出現 `Startup done` + frontend 出現 `Local: http://localhost:5173`，瀏覽器打開 http://localhost:5173。

Masthead 右上角應該顯示 `You are: <你的 label>`，看到代表 `.env` 設對了。

## 常見問題

**Q. 我看得到別人的 watchlist 嗎？**
不會。watchlist / strategies / active_signals / signals_log 全部按 `USER_LABEL` 隔離。市場資料（symbols / 技術指標 / OHLC）才是共用。

**Q. 8:25 盤後的 indicator cache 是誰跑？**
只有 `CACHE_JOB_OWNER` 跟 `USER_LABEL` 相符的那台 backend 會跑——這台一律是 loger 的電腦。如果 loger 那天沒開機，當天 indicator 不會更新，最壞影響是隔天條件式篩股用的是前一交易日資料。

**Q. 我的富邦帳號會被別人用到嗎？**
不會。`.env` 只在你電腦上，富邦 SDK 在你本機 process 內跑。

**Q. 撞名怎辦？**
backend startup 會驗 label 格式，但**不**擋重複——同一個 label 兩個朋友跑會互相覆寫資料。在群組裡先講好。

**Q. 我是 Mac / Linux 怎辦？**
目前不支援。富邦只提供 Windows wheel。

**Q. service_role key 外洩會怎樣？**
拿到 key 的人可以讀寫整個 Supabase（所有人的資料）。請當作密碼保管：不要 commit、不要貼 Discord、不要存在公開雲端硬碟。

## 開發者文件

- `docs/superpowers/specs/2026-05-13-local-userlabel-design.md` — 本地版設計
- `docs/superpowers/plans/2026-05-13-local-userlabel.md` — 實作計劃
- `docs/decisions/` — 重要決策紀錄

## 授權

Personal use only. No warranty. 富邦 SDK 屬富邦證券所有，請依其授權條款使用。
