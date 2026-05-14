-- Strip zombie fields from active_signals.filter_json (2026-05-13)
--
-- `market` / `exclude_etf` / `limit` 三個 key 是 Phase 2b Filter 留下的殭屍欄位,
-- signal_engine 完全不讀。配合 Pydantic Filter slim-down 移除這些 keys。

update active_signals
set filter_json = filter_json - 'market' - 'exclude_etf' - 'limit'
where filter_json ? 'market' or filter_json ? 'exclude_etf' or filter_json ? 'limit';
