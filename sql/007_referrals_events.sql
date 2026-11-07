-- =============================================================================
-- football-analyzer — рефералы и события воронки (007).
--
-- Реферал: кто привёл пользователя. Бонусные дни начисляются пригласившему,
-- когда приглашённый активирует подписку.
--
-- События: шаги воронки (start, consent, tariff_view, buy_click, pay_click).
-- Без них конверсия меряется «на глаз», а решения о продвижении принимаются
-- вслепую.
-- =============================================================================

ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS referred_by BIGINT;
ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS referred_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS events (
    id         BIGSERIAL PRIMARY KEY,
    chat_id    BIGINT,
    name       TEXT NOT NULL,
    meta       JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_events_name ON events (name, created_at);
