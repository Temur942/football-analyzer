-- =============================================================================
-- football-analyzer — платежи ЮKassa (012).
--
-- Зачем своя таблица: оплата подтверждается асинхронно. Запись фиксирует, что
-- человек начал платить, и позволяет дожать статус позже — не терять оплату,
-- если ответ ЮKassa не пришёл в тот же момент (правило из раздела «Деньги»).
-- =============================================================================

CREATE TABLE IF NOT EXISTS payments (
    id           BIGSERIAL PRIMARY KEY,
    chat_id      BIGINT NOT NULL,
    days         INTEGER NOT NULL,
    amount       NUMERIC(10, 2) NOT NULL,
    yookassa_id  TEXT UNIQUE,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending | succeeded | canceled
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_payments_status ON payments (status, created_at);
CREATE INDEX IF NOT EXISTS idx_payments_chat ON payments (chat_id);
