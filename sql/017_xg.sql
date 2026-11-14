-- =============================================================================
-- football-analyzer — ожидаемые голы (xG) (017).
--
-- xG устойчивее фактических голов: команда может забить 0 при 2.1 xG.
-- Используем его как основу для оценок силы, когда источник его даёт
-- (football-data.co.uk: колонки HxG/AxG), иначе считаем по голам.
-- =============================================================================

ALTER TABLE market_odds ADD COLUMN IF NOT EXISTS home_xg NUMERIC(5, 2);
ALTER TABLE market_odds ADD COLUMN IF NOT EXISTS away_xg NUMERIC(5, 2);
ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_xg NUMERIC(5, 2);
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_xg NUMERIC(5, 2);
