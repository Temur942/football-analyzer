-- =============================================================================
-- football-analyzer — подписчики, прогнозы и журнал отправок (004).
--
-- Зачем отдельный журнал отправок: «отправлено» должно быть строкой в базе,
-- а не строкой в логе. Первичный ключ (match_id, chat_id) гарантирует, что
-- перезапуск бота или повторный проход не пришлют один прогноз дважды.
-- =============================================================================

CREATE TABLE IF NOT EXISTS subscribers (
    chat_id     BIGINT PRIMARY KEY,
    username    TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    paid_until  TIMESTAMPTZ,           -- NULL = подписки нет, сообщения не шлём
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS predictions (
    id            BIGSERIAL PRIMARY KEY,
    match_id      BIGINT NOT NULL REFERENCES matches(id),
    model_version TEXT   NOT NULL,
    payload       JSONB  NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (match_id, model_version)
);

CREATE TABLE IF NOT EXISTS deliveries (
    id         BIGSERIAL PRIMARY KEY,
    match_id   BIGINT NOT NULL REFERENCES matches(id),
    chat_id    BIGINT NOT NULL,
    -- sent = отправлено, dry_run = пробный прогон, failed = ошибка
    status     TEXT   NOT NULL,
    error      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (match_id, chat_id)
);

CREATE INDEX IF NOT EXISTS idx_deliveries_status ON deliveries (status, created_at);
CREATE INDEX IF NOT EXISTS idx_predictions_match ON predictions (match_id);
