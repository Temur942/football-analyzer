-- =============================================================================
-- football-analyzer — партнёры и промокоды (019).
--
-- Партнёр — канал или сообщество, которое приводит людей по ссылке
-- ?start=ch_<slug>. Источник уже сохраняется в subscribers.source, поэтому
-- отдельная таблица событий не нужна: статистика считается по нему.
-- =============================================================================

CREATE TABLE IF NOT EXISTS partners (
    slug         TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    channel_url  TEXT,
    audience     TEXT,
    brand_color  TEXT DEFAULT '#e5320f',
    active       BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS promo_codes (
    code         TEXT PRIMARY KEY,
    partner_slug TEXT REFERENCES partners(slug) ON DELETE CASCADE,
    days         INTEGER NOT NULL DEFAULT 7,
    max_uses     INTEGER NOT NULL DEFAULT 50,
    used_count   INTEGER NOT NULL DEFAULT 0,
    active       BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS promo_uses (
    id        BIGSERIAL PRIMARY KEY,
    code      TEXT NOT NULL REFERENCES promo_codes(code) ON DELETE CASCADE,
    chat_id   BIGINT NOT NULL,
    days      INTEGER NOT NULL,
    used_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (code, chat_id)
);
