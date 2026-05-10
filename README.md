# treading-king

個人自用台股篩股工具，整合 [富邦 Neo API](https://www.fbs.com.tw/TradeAPI/docs/welcome) 的量化資料 + 即時行情。

> ⚠️ 這個資料夾目前實體名稱是 `neo-api`（Claude Code session lock 住改不了名）。
> Session 結束後請手動改名：
> ```powershell
> Set-Location C:\side-project
> Move-Item neo-api treading-king
> ```

## 技術棧

- **前端**：Vite + React + TypeScript + Tailwind
- **後端**：FastAPI (Python 3.12)
- **資料庫**：Supabase (Postgres)
- **資料源**：富邦 Neo API（Python SDK，從官網下載 wheel）

## MVP 三大功能

1. **量化篩股**：條件式 + 策略式，全市場 1700+ 檔 cache 後 on-demand 篩
2. **即時篩股**：watchlist + Phase 2 候選股（≤500 檔）的條件觸發訊號
3. **通知**：前端 WebSocket + Discord Webhook

詳細設計見：`C:\Users\USER\.claude\plans\neo-api-https-github-com-phenomenoner-n-modular-newt.md`

---

## 日常 dev 啟動（已完成 Phase 1 後）

需要兩個 PowerShell 視窗。

**Terminal A — Backend（:8000）**
```powershell
cd C:\side-project\neo-api\backend
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

**Terminal B — Frontend（:5173）**
```powershell
cd C:\side-project\neo-api\frontend
npm run dev
```

開瀏覽器：
- http://localhost:5173 — 前端 React app（主畫面）
- http://localhost:8000/docs — Backend 自動產生的 Swagger UI（測 API 用）
- http://localhost:8000/api/health — JSON health check

關掉服務直接 Ctrl+C 該視窗即可。

---

## Phase 0 — 倉庫起手式（已完成）

- [x] 建立目錄結構（`backend/`, `frontend/`, `supabase/`, `logs/`）
- [x] `.gitignore`
- [x] `README.md`
- [x] `backend/.env.example`
- [x] `backend/scripts/sdk_smoke.py`
- [x] git init + remote + 第一個 commit
- [x] 從富邦官網下載 SDK wheel `backend/wheels/fubon_neo-2.2.8-cp37-abi3-win_amd64.whl`

## Phase 0.5 — SDK Sanity Check（**Day 1 必跑**）

驗證 4 個 plan 假設：wheel 安裝、apikey_login 存在、Technical 5 個 endpoint、WS 200 上限。

### 0.5.1 — 你需要先做這兩件事

1. **下載富邦 SDK wheel**
   - 到富邦 TradeAPI 官網下載最新 v2.x wheel（注意是 cp312 或 cp313 對應 Python 版本）
   - 放到 `backend/wheels/fubon_neo-x.y.z-cpXX-XXX.whl`

2. **填 `backend/.env`**
   ```powershell
   Copy-Item backend\.env.example backend\.env
   notepad backend\.env  # 填入 FUBON_API_KEY
   ```
   `.env` 不會進 git（已在 `.gitignore`）。

### 0.5.2 — 跑 sanity check（之後我會自動做）

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install ./wheels/fubon_neo-*.whl python-dotenv
python scripts/sdk_smoke.py
```

**通過標準**：5 步全部 PASS → 進 Phase 1。任何一步失敗 → 找文件 / 換 wheel 版本 / 連絡富邦。

---

## 後續 Phase

- **Phase 1**：基礎建設（FastAPI + Vite + Supabase 連通 + 單檔 quote）
- **Phase 2a**：盤後 indicator cache job + 端到端煙霧（hard-code RSI 篩股）
- **Phase 2b**：條件 DSL + 策略範本 + UI
- **Phase 2.5** *(optional)*：每日 16:30 自動跑量化篩股
- **Phase 3**：即時篩股（WS pool + ring buffer + signal engine + Discord）

每階段詳細設計見 plan 檔。

---

## 目錄結構

```
treading-king/
├── backend/
│   ├── wheels/             ← 富邦 wheel（不進 git）
│   ├── scripts/            ← 一次性工具（sdk_smoke.py）
│   ├── routes/             ← FastAPI endpoints
│   ├── services/           ← 業務邏輯
│   ├── models/             ← Pydantic schemas
│   ├── middleware/         ← X-API-Key auth 等
│   └── jobs/               ← 排程 job (Phase 2.5)
├── frontend/               ← Vite + React + TS
├── supabase/migrations/    ← SQL migrations
├── logs/                   ← rotating log files（不進 git）
├── .gitignore
└── README.md
```

## 安全性提醒

- 富邦 API Key 永遠在 `backend/.env`，不進 git、不進前端 build
- 前端 `VITE_*` 變數會打包進 JS bundle（公開），不能放任何 secret
- 部署到雲端前，把 X-API-Key middleware 換成 supabase auth 或 basic auth（plan §部署）
