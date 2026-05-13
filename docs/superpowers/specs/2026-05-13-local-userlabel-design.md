# 本地版 + 共用 Supabase + user_label 隔離 — 設計文件

- 日期：2026-05-13
- 作者：loger
- 狀態：approved（pending 寫 plan）

## 目標

讓 2~5 個信任朋友能在自己 Windows 電腦本機跑這個 trading-king 專案，共用同一個 Supabase + 各自富邦 API key，靠 `.env` 的 `USER_LABEL` 在資料表上做命名空間隔離。

## 為什麼不部署到雲端

- 富邦 SDK wheel 是 `fubon_neo-2.2.8-cp37-abi3-win_amd64.whl`，只能裝在 Windows x64；Railway / Render / Fly / Lambda 等 Linux 平台都裝不起來。
- Vercel 是 serverless，跑不了長駐 FastAPI + WebSocket。
- 富邦 SDK 在同 process 是 singleton，無法水平擴展（5 connections × 200 symbols 需要 multi-process）。
- 富邦 API key 是個人帳號，雲端共用會違反一帳號一連線假設。

「本地跑 + 各自富邦帳號」反而把現在程式碼的所有單租戶毛病轉成正解。

## 設計決定（已拍板）

| 決定 | 選定方案 |
| --- | --- |
| 使用者規模 | 我（loger）+ 2~5 個信任的朋友 |
| 分表規劃 | `symbols` / `indicators_daily` / `daily_ohlc` / `cache_runs` 共用；`watchlist` / `strategies` / `active_signals` / `signals_log` 加 `user_label` 分開 |
| Label 注入 | backend `.env` 讀 `USER_LABEL`，啟動 cache 一次，所有 query 自動 inject |
| Cache job 歸屬 | 只有 `USER_LABEL == CACHE_JOB_OWNER` 的 instance 會跑 8:25 cron（OWNER = `loger`） |
| 分發 | 公開 GitHub repo + README 教學 |
| Supabase key | service_role，私訊給朋友填 `.env`；不重寫 RLS |
| 富邦 wheel | 從 repo 移除，README 教朋友自己去富邦官網下載 |
| 既有資料 | `default 'loger'` backfill，之後 drop default |
| Label 驗證規則 | `[a-z0-9_-]{2,20}`，違反直接 startup fail |

## §1 架構

```
朋友 A 電腦               朋友 B 電腦              loger 電腦 (OWNER)
─────────────             ─────────────            ──────────────────
:5173 (Vite)              :5173                    :5173
   ↕                         ↕                        ↕
:8000 (FastAPI)            :8000                    :8000
   ├── 富邦 SDK (A key)    ├── 富邦 SDK (B key)    ├── 富邦 SDK (loger key)
   │                       │                       ├── CACHE_JOB_OWNER=loger
   │                       │                       │   → 8:25 跑 indicator cache
   └── Supabase (service_role) ─── 共用一張 DB，靠 user_label 區隔
```

**關鍵性質**

- 每個 backend 是 single-tenant：啟動讀 `USER_LABEL`，所有 watchlist / strategies / active_signals / signals_log 的 query 都自動 `.eq("user_label", LABEL)`。
- WSPool / signal_engine / broadcaster 不變——它們本來就只服務「這台 backend」，per-user 跑反而把多租戶問題消滅。
- 富邦帳號完全獨立（各自 .env），WS 200 sub 上限是各自的，不互相佔額度。
- 共用 Supabase 的價值：盤後 indicator cache 算一次大家共用；新使用者 git clone 完馬上就有 1700 檔可篩，不用各自再灌一次。

## §2 Schema 變動

### 新 migration：`supabase/migrations/0005_user_label.sql`

```sql
-- watchlist: PK 從 (symbol) 改為 (user_label, symbol)
alter table watchlist add column user_label text not null default 'loger';
alter table watchlist drop constraint watchlist_pkey;
alter table watchlist add primary key (user_label, symbol);
alter table watchlist alter column user_label drop default;
create index idx_watchlist_label on watchlist(user_label);

-- strategies: 加欄位（id 已是 uuid PK）
alter table strategies add column user_label text not null default 'loger';
alter table strategies alter column user_label drop default;
create index idx_strategies_label on strategies(user_label, created_at desc);

-- active_signals
alter table active_signals add column user_label text not null default 'loger';
alter table active_signals alter column user_label drop default;
create index idx_active_signals_label_enabled
  on active_signals(user_label, enabled) where enabled;

-- signals_log
alter table signals_log add column user_label text not null default 'loger';
alter table signals_log alter column user_label drop default;
create index idx_signals_log_label_time on signals_log(user_label, triggered_at desc);
```

### 不動的表

`symbols`、`indicators_daily`、`daily_ohlc`、`cache_runs`、`fubon_*` 等共用市場資料表完全不動。

### RLS

維持現狀（`anon can read ...` policy）。backend 用 service_role bypass RLS，不加「按 user_label 限讀」policy，理由：
- service_role 反正 bypass
- 之後 admin endpoint / Supabase dashboard 查詢需要看全表
- 信任前提下靠 backend 一致 inject 即可

### Backfill

migration 的 `default 'loger'` 在 ADD COLUMN 那一刻把所有既有 row backfill 為 `loger`；之後 `drop default` 強制新 INSERT 必須顯式提供 label，避免 backend 程式忘記帶被靜默成 `loger` 髒資料。

### 風險

1. **watchlist PK 變動**：從 `symbol` 變 `(user_label, symbol)`。已搜過 backend code，沒有依賴單欄唯一性的 query。
2. **舊單欄 index**（`idx_signals_log_triggered_desc` 等）保留不動；之後 explain analyze 再決定是否刪。

## §3 Backend Code 變動

### 新檔：`backend/services/user_context.py`

```python
import os, re
from functools import lru_cache

_LABEL_RE = re.compile(r"^[a-z0-9_-]{2,20}$")

@lru_cache(maxsize=1)
def get_user_label() -> str:
    raw = (os.getenv("USER_LABEL") or "").strip()
    if not _LABEL_RE.match(raw):
        raise RuntimeError(
            f"USER_LABEL invalid: {raw!r}. Must match [a-z0-9_-]{{2,20}}."
        )
    return raw

def is_cache_job_owner() -> bool:
    owner = (os.getenv("CACHE_JOB_OWNER") or "").strip().lower()
    return owner == get_user_label()
```

`main.py` lifespan 開頭 call `get_user_label()` 一次——壞 label 直接 startup fail。

### 改動檔案

| 檔案 | 改動 |
| --- | --- |
| `backend/main.py` | L55-66 startup watchlist subscribe 加 `.eq("user_label", LABEL)`；L69 `overnight_loop` 只在 `is_cache_job_owner()` 為 True 時 `create_task`；lifespan 開頭驗證 `get_user_label()` |
| `backend/routes/watchlist.py` | 所有 `.table("watchlist")` 的 SELECT / INSERT / DELETE 加 label 過濾 |
| `backend/routes/strategies.py` | 同上 |
| `backend/routes/active_signals.py` | 同上 |
| `backend/routes/signals_history.py` | GET 加 `.eq("user_label", LABEL)` |
| `backend/routes/cache.py` | `POST /api/cache/refresh` 非 OWNER 回 403 `{"error": "not_cache_owner"}` |
| `backend/services/signal_engine.py` | `refresh_active_signals()` 加 label 過濾；`signals_log` INSERT 帶 label；scope=watchlist 解析時撈 watchlist 也加 label 過濾 |
| `backend/services/supabase_writer.py` | 若有獨立寫 signals_log 路徑，payload 統一帶 label |
| `backend/routes/health.py` | 回傳 payload 加 `user_label` 欄位 |
| `backend/routes/me.py`（新檔） | `GET /api/me` 回 `{user_label, is_cache_owner}` |
| `backend/main.py` | include `me.router` |
| `backend/.env.example` | 加 `USER_LABEL=` / `CACHE_JOB_OWNER=` 兩行（含中文註解） |

### 完全不動

- `fubon_ws.py`、`ws_broadcaster.py`、`routes/ws.py`：per-instance，本來就只服務本機。
- `routes/quote.py` / `candles.py` / `cdp.py` / `preview.py` / `symbols.py` / `screen.py`：純市場資料或共用表查詢。

## §4 Frontend + 分發

### Frontend

| 檔案 | 改動 |
| --- | --- |
| `frontend/src/lib/api.ts` | 加 `MeResponse` 型別 + `api.me()` |
| `frontend/src/hooks/useMe.ts`（新檔） | App 載入 fetch 一次，cache 整個 session |
| `frontend/src/App.tsx`（或 Masthead 元件） | 顯示 `You are: <label>`，cache owner 多顯示小徽章；位置在現有 health badge 旁邊 |
| `frontend/.env.example` | 不動 |

`useWatchlist` / `useActiveSignals` / `useTodayHits` / `useSignalsStream` 全不動——backend 自動過濾，前端拿到的就只有自己的。

### 分發資產

**`README.md`** — 改寫為「面向使用者」版本，內容包含：
- 前置需求：Windows 10/11 x64、富邦 TradeAPI 帳號、Python 3.12、Node.js 20+、Git、Supabase service_role key（向 loger 索取）、自選 USER_LABEL
- 安裝步驟（git clone → 下載 wheel → 填 .env → `install.ps1` → `start.ps1`）
- FAQ：8:25 cache 誰跑 / watchlist 隔離 / 富邦帳號隔離

**`install.ps1`**（repo 根目錄）— 建 venv、pip install wheel、pip install -e .、npm install；找不到 wheel 直接報錯指引使用者去富邦官網。

**`start.ps1`**（repo 根目錄）— 檢查 `USER_LABEL` 已設，否則直接 abort；通過後開兩個 PowerShell 視窗各跑 backend / frontend。

**`.gitignore`** — 加 `backend/wheels/*.whl`；以 `git rm --cached` 把現有 wheel 從 tracking 移除（git history 仍會殘留，但非敏感資料）。

### Repo 公開前 checklist（提醒）

- 檢查 `.env`、log、`docs/decisions/` 是否有外流的 key / 帳號名
- 檢查 git history 是否 commit 過 key：`git log --all -p | grep -i "api_key\|service_role"`
- README 加 licence（建議 MIT 或寫明「personal use only, no warranty」）

## §5 測試策略

### 1. Migration smoke test（手動，必跑一次）

apply 完 0005 後跑：

```sql
select 'watchlist' as t,
       count(*) filter (where user_label='loger') as loger,
       count(*) filter (where user_label is null) as nulls
from watchlist
union all
select 'strategies', count(*) filter (where user_label='loger'),
       count(*) filter (where user_label is null) from strategies
union all
select 'active_signals', count(*) filter (where user_label='loger'),
       count(*) filter (where user_label is null) from active_signals
union all
select 'signals_log', count(*) filter (where user_label='loger'),
       count(*) filter (where user_label is null) from signals_log;
```

`nulls` 必須全部 0。

### 2. Backend unit test — `backend/tests/test_user_context.py`

- valid `"loger"` → OK
- invalid: `""`、`"Loger"`（大寫）、`"foo bar"`（空格）、21 字、`"a"`（1 字） → raise RuntimeError
- `is_cache_job_owner()` OWNER 相符回 True、不符回 False、空字串 OWNER 回 False

### 3. Backend integration smoke — `backend/tests/test_label_isolation.py`

- 兩個 testclient instance（mock env 切換）：`USER_LABEL=alice` 與 `USER_LABEL=bob`
- alice POST watchlist 2330，bob GET watchlist 不應該看到 2330
- alice POST strategy，bob list 不應看到
- alice 觸發 signal → signals_log 寫入帶 alice，bob `/api/signals/history` 不應看到

### 4. Cache job 隔離手測

- `CACHE_JOB_OWNER=loger USER_LABEL=loger`：log 出現 `overnight loop started`
- `CACHE_JOB_OWNER=loger USER_LABEL=alice`：log 出現 `cache job skipped (not owner)`

### 5. 手動端對端 dogfood

從乾淨資料夾用測試 label `tester` git clone → 照 README 走一遍，抓 README 拼字 / 缺步驟。

## Rollout 順序

1. **PR 1**：migration 0005 + `user_context.py` + 改 routes / services + unit + integration tests
2. 在 Supabase 上 apply 0005（先 dev branch 試）
3. 本機跑 backend，驗自己原有資料 / 功能無回歸
4. **PR 2**：`install.ps1` / `start.ps1` / README 改寫 / `.gitignore` + `git rm --cached` wheel
5. 用測試 .env (`USER_LABEL=tester`) dogfood onboarding 全流程
6. **PR 3**：repo 改 public + 私訊朋友 keys

## Out-of-Scope

- 多人 user model + Supabase auth（之後對陌生人開放再做）
- `signals_log` retention / TTL
- RLS 嚴格化（service_role bypass，現不需）
- 朋友自己跑 cache job 的能力
- Mac / Linux 支援（富邦 wheel 限制）
- PyInstaller 打包 exe
- Admin endpoint 列出所有 user_label（暫時用 Supabase dashboard 直接看）

## 已知 trade-offs

- **OWNER 沒開電腦那天 cache 沒更新**：可接受，最壞情況朋友隔天拿到的還是前一交易日資料。
- **service_role 等同 admin**：信任 2~5 朋友前提下接受，README 警告勿外流。
- **共用 Supabase 容量**：free tier 500MB；signals_log 主要增長源，估 5 人 × 200 筆/天 ≈ 30 萬筆/年，遠低於上限；超過時再加 retention。
- **撞名**：靠口頭協調 + startup validation 擋空字串 / 格式錯，不擋重複；同一 label 兩台 backend 同時跑會互相覆寫 watchlist。
