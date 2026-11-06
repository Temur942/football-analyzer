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
SCHEMA_DIR = PROJECT_ROOT / "sql"

# После стольких неудач матч перестаём дёргать: повтор даст тот же ответ.
MAX_STATS_ATTEMPTS = 3


async def connect(dsn: str) -> asyncpg.Pool:
    """Пул соединений. На 2 ГБ RAM хватает 1–5 соединений."""
    return await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5, command_timeout=30)


async def apply_schema(pool: asyncpg.Pool, directory: Path = SCHEMA_DIR) -> list[Path]:
    """Применить все миграции из каталога по порядку имён.

    Повторный запуск безопасен: каждая миграция аддитивная
    (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).
    """
    files = await asyncio.to_thread(lambda: sorted(directory.glob("*.sql")))
    if not files:
        raise RuntimeError("не найдено ни одного файла схемы в %s" % directory)
    # Чтение файлов — блокирующая операция, уводим в поток, чтобы не вешать цикл.
    texts = await asyncio.to_thread(
        lambda: [(path, path.read_text(encoding="utf-8")) for path in files]
    )
    async with pool.acquire() as conn:
        for _path, sql in texts:
            await conn.execute(sql)
    log.info("схема применена: %s", ", ".join(path.name for path, _ in texts))
    return [path for path, _ in texts]


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
                    status, source, source_game_id, dedupe_key, home_goals, away_goals
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (dedupe_key) DO UPDATE SET
                    kickoff_utc = EXCLUDED.kickoff_utc,
                    status = EXCLUDED.status,
                    source_game_id = EXCLUDED.source_game_id,
                    home_goals = COALESCE(EXCLUDED.home_goals, matches.home_goals),
                    away_goals = COALESCE(EXCLUDED.away_goals, matches.away_goals),
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
                fixture.home_goals,
                fixture.away_goals,
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


async def pending_stats(
    pool: asyncpg.Pool, limit: int, max_attempts: int = MAX_STATS_ATTEMPTS
) -> list[asyncpg.Record]:
    """Матчи, у которых ещё нет статистики (возобновляемая выгрузка).

    Выбираются только завершённые матчи с известным id в источнике.
    Матчи, у которых уже было max_attempts неудач, пропускаются: иначе
    один «мёртвый» матч будет пытаться скачаться вечно.
    """
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT m.id, m.source_game_id, c.source_key AS espn_slug
              FROM matches m
              JOIN competitions c ON c.id = m.competition_id
             WHERE m.stats_fetched_at IS NULL
               AND m.stats_attempts < $2
               AND m.source_game_id IS NOT NULL
               AND m.status = 'finished'
             ORDER BY m.kickoff_utc DESC
             LIMIT $1
            """,
            limit,
            max_attempts,
        )


async def save_stats(pool: asyncpg.Pool, match_id: int, stats: object) -> None:
    """Записать статистику матча и отметить его обработанным."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE matches SET
                home_goals = COALESCE($2, home_goals),
                away_goals = COALESCE($3, away_goals),
                home_goals_ht = $4,
                away_goals_ht = $5,
                home_corners = $6,
                away_corners = $7,
                home_yellow = $8,
                away_yellow = $9,
                home_red = $10,
                away_red = $11,
                stats_fetched_at = now(),
                stats_error = NULL,
                updated_at = now()
             WHERE id = $1
            """,
            match_id,
            stats.home_goals,  # type: ignore[attr-defined]
            stats.away_goals,  # type: ignore[attr-defined]
            stats.home_goals_ht,  # type: ignore[attr-defined]
            stats.away_goals_ht,  # type: ignore[attr-defined]
            stats.home_corners,  # type: ignore[attr-defined]
            stats.away_corners,  # type: ignore[attr-defined]
            stats.home_yellow,  # type: ignore[attr-defined]
            stats.away_yellow,  # type: ignore[attr-defined]
            stats.home_red,  # type: ignore[attr-defined]
            stats.away_red,  # type: ignore[attr-defined]
        )


async def mark_stats_error(pool: asyncpg.Pool, match_id: int, error: str) -> None:
    """Отметить неудачную попытку, не помечая матч обработанным."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE matches
               SET stats_attempts = stats_attempts + 1,
                   stats_error = $2,
                   updated_at = now()
             WHERE id = $1
            """,
            match_id,
            error[:500],
        )


async def mark_stats_unavailable(pool: asyncpg.Pool, match_id: int, reason: str) -> None:
    """Источник не хранит статистику по этому матчу — больше не пытаемся.

    Это не сбой сети: ESPN отдаёт boxscore с нулём статистик. Повтор запроса
    вернёт то же самое, поэтому попытки сразу исчерпываются.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE matches
               SET stats_attempts = $3,
                   stats_error = $2,
                   updated_at = now()
             WHERE id = $1
            """,
            match_id,
            reason[:500],
            MAX_STATS_ATTEMPTS,
        )


async def stats_progress(pool: asyncpg.Pool) -> dict[str, int]:
    """Сколько всего матчей, сколько с статистикой и сколько ждёт."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT count(*) AS total,
                   count(stats_fetched_at) AS done,
                   count(*) FILTER (
                       WHERE stats_fetched_at IS NULL
                         AND stats_attempts < 3
                         AND source_game_id IS NOT NULL
                         AND status = 'finished'
                   ) AS pending,
                   count(*) FILTER (
                       WHERE stats_error IS NOT NULL AND stats_attempts >= 3
                   ) AS failed
              FROM matches
            """
        )
    return {key: int(row[key] or 0) for key in ("total", "done", "pending", "failed")}
