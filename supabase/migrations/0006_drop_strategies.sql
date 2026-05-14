-- Drop strategies table — Screener 功能完全移除 (2026-05-13)
--
-- Phase 2b 條件編輯器 + 策略 CRUD 整套下架。即時訊號(active_signals)獨立,
-- 不依賴 strategies 表。models/condition.py 的 Filter / Condition DSL 保留,
-- 給 ActiveFilter 繼承。
--
-- 0003_strategies_watchlist.sql 之 watchlist 部分仍然保留(現在的核心功能)。

drop table if exists strategies cascade;
