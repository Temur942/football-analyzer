-- =============================================================================
-- football-analyzer — статистика завершённых матчей (002).
--
-- Только аддитивные изменения: ADD COLUMN IF NOT EXISTS.
-- Заполняется проходом `sync-stats` из ESPN summary.
-- `stats_fetched_at` — маркер «этот матч уже обработан»; он же делает выгрузку
-- возобновляемой: обрыв не заставляет начинать заново.
-- =============================================================================

ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_goals_ht   INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_goals_ht   INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_corners    INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_corners    INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_yellow     INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_yellow     INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_red        INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_red        INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS stats_fetched_at TIMESTAMPTZ;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS stats_error      TEXT;
-- Сколько раз пытались получить статистику: после 3 неудач матч не трогаем.
ALTER TABLE matches ADD COLUMN IF NOT EXISTS stats_attempts   INTEGER NOT NULL DEFAULT 0;

-- Индекс по «ещё не обработанным»: по нему идёт выборка в sync-stats.
CREATE INDEX IF NOT EXISTS idx_matches_stats_pending
    ON matches (kickoff_utc)
    WHERE stats_fetched_at IS NULL AND source_game_id IS NOT NULL;

-- Быстрые выборки для модели: последние матчи команды.
CREATE INDEX IF NOT EXISTS idx_matches_home_team ON matches (home_team, kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_matches_away_team ON matches (away_team, kickoff_utc);
