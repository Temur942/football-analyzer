-- =============================================================================
-- football-analyzer — «ценные» линии для администратора (014).
--
-- Отдельная таблица, а не журнал доставок: админ получает и полный прогноз
-- (deliveries), и дополнительную выжимку по линиям с коэффициентом 1,7+.
-- Ключ (match_id, chat_id) в deliveries не даёт хранить два сообщения на матч.
-- =============================================================================

CREATE TABLE IF NOT EXISTS value_alerts (
    match_id BIGINT NOT NULL,
    chat_id  BIGINT NOT NULL,
    sent_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (match_id, chat_id)
);
