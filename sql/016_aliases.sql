-- =============================================================================
-- football-analyzer — соответствие названий команд (016).
--
-- Источники пишут по-разному: ESPN — «Manchester City», football-data —
-- «Man City». Сопоставление ручным словарём не масштабируется, поэтому
-- строим таблицу соответствий автоматически и храним с оценкой похожести.
-- =============================================================================

CREATE TABLE IF NOT EXISTS team_aliases (
    competition_id TEXT NOT NULL,
    source_name    TEXT NOT NULL,
    our_name       TEXT NOT NULL,
    score          NUMERIC(4, 3),
    PRIMARY KEY (competition_id, source_name)
);
