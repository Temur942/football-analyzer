-- =============================================================================
-- football-analyzer — начальная схема (PostgreSQL).
--
-- Миграции ТОЛЬКО аддитивные: CREATE TABLE IF NOT EXISTS,
-- ALTER TABLE ... ADD COLUMN IF NOT EXISTS. База может быть общей у стейджа и
-- прода, поэтому ничего не удаляем и не переименовываем без явной команды.
--
-- Применение: python -m football_analyzer.cli migrate
-- =============================================================================

CREATE TABLE IF NOT EXISTS competitions (
    id          TEXT PRIMARY KEY,            -- внутренний код: eng-premier-league
    name_ru     TEXT        NOT NULL,        -- «АПЛ (Англия)»
    country     TEXT,                        -- England
    tier        INTEGER,                     -- 1 — высший дивизион, 2 — второй
    source      TEXT        NOT NULL DEFAULT 'ESPN',
    source_key  TEXT,                        -- ключ в источнике: eng.1
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS matches (
    id             BIGSERIAL PRIMARY KEY,
    competition_id TEXT        NOT NULL REFERENCES competitions(id),
    season         TEXT        NOT NULL,
    kickoff_utc    TIMESTAMPTZ NOT NULL,
    home_team      TEXT        NOT NULL,
    away_team      TEXT        NOT NULL,
    home_goals     INTEGER,
    away_goals     INTEGER,
    status         TEXT        NOT NULL DEFAULT 'scheduled',
    source         TEXT        NOT NULL,
    source_game_id TEXT,
    -- Матч уникален в рамках соревнования и сезона по времени и участникам.
    -- Это защита от повторной вставки при перезапуске сбора.
    dedupe_key     TEXT        NOT NULL,
    raw            JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT matches_dedupe_key_uniq UNIQUE (dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_matches_kickoff ON matches (kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_matches_competition
    ON matches (competition_id, kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_matches_status ON matches (status, kickoff_utc);

-- -----------------------------------------------------------------------------
-- Аренды долгих операций. Блокировка в памяти процесса не защищает от
-- параллельного прохода и перезапуска — аренда живёт в базе и истекает сама.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS leases (
    name        TEXT PRIMARY KEY,
    owner       TEXT        NOT NULL,
    lease_until TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- Журнал сбора расписания: доказательство работы — строка в базе, а не лог.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingest_runs (
    id           BIGSERIAL PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    source       TEXT        NOT NULL,
    season       TEXT,
    status       TEXT        NOT NULL DEFAULT 'running',
    rows_seen    INTEGER     NOT NULL DEFAULT 0,
    rows_new     INTEGER     NOT NULL DEFAULT 0,
    rows_updated INTEGER     NOT NULL DEFAULT 0,
    error        TEXT
);
