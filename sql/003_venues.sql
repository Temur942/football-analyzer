-- =============================================================================
-- football-analyzer — стадионы команд (003).
--
-- ESPN отдаёт только город стадиона, координат нет. Город геокодируется через
-- Open-Meteo один раз и кэшируется здесь: погоду запрашиваем по координатам.
-- =============================================================================

CREATE TABLE IF NOT EXISTS venues (
    team       TEXT PRIMARY KEY,
    city       TEXT,
    country    TEXT,
    lat        DOUBLE PRECISION,
    lon        DOUBLE PRECISION,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
