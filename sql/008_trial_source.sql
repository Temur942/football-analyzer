-- =============================================================================
-- football-analyzer — пробный доступ и источник прихода (008).
--
-- trial_used: чтобы 3 бесплатных дня нельзя было получить повторно.
-- source: метка канала привлечения (?start=ch_<код>) — без неё не понять,
-- какой источник реально приводит платящих.
-- =============================================================================

ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS trial_used BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS source TEXT;
