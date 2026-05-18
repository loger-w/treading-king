-- Drop indicator_cache + indicator_cache_runs
--
-- 2026-05-18:全市場 indicator batch cache 退役。
-- MA 線改 on-demand 從富邦 tech.sma 即時拿;訊號引擎 condition 欄位縮到 close + cdp_*。
-- 詳見 backend/routes/ma.py + backend/services/signal_engine.py 的新實作。

drop table if exists indicator_cache_runs cascade;
drop table if exists indicator_cache cascade;
