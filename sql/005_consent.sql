-- =============================================================================
-- football-analyzer — согласие на обработку ПД (005).
--
-- По 152-ФЗ согласие должно быть конкретным и подтверждённым. Храним версию
-- текста, время согласия и подтверждение 18+: это доказательство, которое
-- нельзя восстановить задним числом.
--
-- Запись создаётся до оплаты: подписки может не быть, а согласие уже нужно.
-- =============================================================================

ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS consent_version TEXT;
ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS consent_at      TIMESTAMPTZ;
ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS age_confirmed   BOOLEAN NOT NULL DEFAULT FALSE;
