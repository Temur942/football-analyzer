-- =============================================================================
-- football-analyzer — исторические коэффициенты (015).
--
-- Источник: football-data.co.uk — публичные CSV с закрывающими коэффициентами
-- (Bet365, Pinnacle, средние и максимальные по рынку), судьёй, xG, углами и
-- карточками. Бесплатно и без брендов конкретной БК.
--
-- Зачем: проверить, есть ли у модели преимущество над рынком. Это внутренняя
-- аналитика, наружу коэффициенты не отдаём.
-- =============================================================================

CREATE TABLE IF NOT EXISTS market_odds (
    id          BIGSERIAL PRIMARY KEY,
    competition TEXT NOT NULL,
    season      TEXT NOT NULL,
    match_date  DATE NOT NULL,
    home_team   TEXT NOT NULL,
    away_team   TEXT NOT NULL,
    home_goals  INTEGER,
    away_goals  INTEGER,
    odds_home   NUMERIC(6, 3),
    odds_draw   NUMERIC(6, 3),
    odds_away   NUMERIC(6, 3),
    avg_home    NUMERIC(6, 3),
    avg_draw    NUMERIC(6, 3),
    avg_away    NUMERIC(6, 3),
    max_home    NUMERIC(6, 3),
    max_draw    NUMERIC(6, 3),
    max_away    NUMERIC(6, 3),
    over25      NUMERIC(6, 3),
    under25     NUMERIC(6, 3),
    UNIQUE (competition, match_date, home_team, away_team)
);
