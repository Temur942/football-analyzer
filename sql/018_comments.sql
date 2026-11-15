-- =============================================================================
-- football-analyzer — комментарии к матчам (018).
--
-- Автор — подписчик или посетитель, вошедший через Telegram. Без авторизации
-- писать нельзя: иначе через неделю будет спам.
-- =============================================================================

CREATE TABLE IF NOT EXISTS comments (
    id           BIGSERIAL PRIMARY KEY,
    subject      TEXT NOT NULL,
    chat_id      BIGINT NOT NULL,
    display_name TEXT,
    body         TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    hidden       BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS comments_subject_idx ON comments (subject, created_at DESC);
