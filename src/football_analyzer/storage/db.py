"""Слой БД: PostgreSQL через asyncpg.

Правила (AGENTS.md §6):
- миграции только аддитивные;
- запись подтверждается запросом к базе, а не строкой в логе;
- долгие операции берут аренду в базе, а не блокировку в памяти процесса.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path

import asyncpg

from football_analyzer.models import Competition, Fixture

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = PROJECT_ROOT / "sql" / "001_init.sql"


async def connect(dsn: str) -> asyncpg.Pool:
    """Пул соединений. На 2 ГБ RAM хватает 1–5 соединений."""
    return await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5, command_timeout=30)


async def apply_schema(pool: asyncpg.Pool, path: Path = SCHEMA_PATH) -> None:
    """Применить схему. Повторный запуск безопасен (IF NOT EXISTS)."""
    # Чтение файла — блокирующая операция, уводим в поток, чтобы не вешать цикл.
    sql = await asyncio.to_thread(path.read_text, encoding="utf-8")
    async with pool.acquire() as conn:
        await conn.execute(sql)
    log.info("схема применена: %s", path)


async def upsert_competitions(pool: asyncpg.Pool, competitions: list[Competition]) -> int:
    """Записать/обновить справочник соревнований. Возвращает число записей."""
    async with pool.acquire() as conn, conn.transaction():
        for competition in competitions:
            await conn.execute(
                """
                INSERT INTO competitions
                    (id, name_ru, country, tier, source, source_key, is_active, updated_at)
                VALUES ($1, $2, $3, $4, 'ESPN', $5, $6, now())
                ON CONFLICT (id) DO UPDATE SET
                    name_ru = EXCLUDED.name_ru,
                    country = EXCLUDED.country,
                    tier = EXCLUDED.tier,
                    source = EXCLUDED.source,
                    source_key = EXCLUDED.source_key,
                    is_active = EXCLUDED.is_active,
                    updated_at = now()
                """,
                competition.id,
                competition.name_ru,
                competition.country,
                competition.tier,
                competition.espn,
                competition.is_active,
            )
    return len(competitions)


async def upsert_fixtures(pool: asyncpg.Pool, fixtures: list[Fixture]) -> tuple[int, int]:
    """Записать матчи. Возвращает (новых, обновлённых).

    Повторный проход не создаёт дубликатов: ключ dedupe_key уникален,
    а конфликт обновляет время, статус и id в источнике.
    """
    new_count = 0
    updated_count = 0
    async with pool.acquire() as conn, conn.transaction():
        for fixture in fixtures:
            row = await conn.fetchrow(
                """
                INSERT INTO matches (
                    competition_id, season, kickoff_utc, home_team, away_team,
                    status, source, source_game_id, dedupe_key
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (dedupe_key) DO UPDATE SET
                    kickoff_utc = EXCLUDED.kickoff_utc,
                    status = EXCLUDED.status,
                    source_game_id = EXCLUDED.source_game_id,
                    updated_at = now()
                RETURNING (xmax = 0) AS inserted
                """,
                fixture.competition_id,
                fixture.season,
                fixture.kickoff_utc,
                fixture.home_team,
                fixture.away_team,
                fixture.status,
                fixture.source,
                fixture.source_game_id,
                fixture.dedupe_key,
            )
            if row["inserted"]:
                new_count += 1
            else:
                updated_count += 1
    return new_count, updated_count


async def count_matches(pool: asyncpg.Pool, days_ahead: int = 3) -> int:
    """Сколько матчей в базе на окно вперёд — для проверки, что запись живая.

    Интервал передаётся как timedelta: asyncpg сам приведёт его к interval.
    Число в SQL здесь опасно — asyncpg отдаёт bigint, а make_interval ждёт int.
    """
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            """
            SELECT count(*) FROM matches
            WHERE kickoff_utc >= now()
              AND kickoff_utc <= now() + $1
            """,
            timedelta(days=days_ahead),
        )
    return int(value or 0)


async def start_ingest_run(pool: asyncpg.Pool, source: str, season: str | None) -> int:
    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            "INSERT INTO ingest_runs (source, season) VALUES ($1, $2) RETURNING id",
            source,
            season,
        )
    return int(run_id)


async def finish_ingest_run(
    pool: asyncpg.Pool,
    run_id: int,
    *,
    status: str,
    seen: int,
    new_count: int,
    updated_count: int,
    error: str | None = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE ingest_runs
               SET finished_at = now(), status = $2, rows_seen = $3,
                   rows_new = $4, rows_updated = $5, error = $6
             WHERE id = $1
            """,
            run_id,
            status,
            seen,
            new_count,
            updated_count,
            error,
        )
