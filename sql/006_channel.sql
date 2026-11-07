-- =============================================================================
-- football-analyzer — очередь публикаций в канал (006).
--
-- Зачем очередь, а не генерация «на лету»: контент-план утверждён заранее,
-- и пост должен уйти в 10:00 и 18:00 МСК даже если в этот момент нет матча
-- для прогноза. kind='forecast' — текст строится по ближайшему матчу.
-- =============================================================================

CREATE TABLE IF NOT EXISTS channel_posts (
    id           BIGSERIAL PRIMARY KEY,
    kind         TEXT NOT NULL DEFAULT 'text',   -- text | forecast
    body         TEXT,
    published_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_channel_posts_pending
    ON channel_posts (id) WHERE published_at IS NULL;
