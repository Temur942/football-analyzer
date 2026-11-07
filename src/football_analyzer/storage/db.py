"""Слой БД: PostgreSQL через asyncpg.

Правила (AGENTS.md §6):
- миграции только аддитивные;
- запись подтверждается запросом к базе, а не строкой в логе;
- долгие операции берут аренду в базе, а не блокировку в памяти процесса.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

import asyncpg

from football_analyzer.models import Competition, Fixture

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = PROJECT_ROOT / "sql"

# После стольких неудач матч перестаём дёргать: повтор даст тот же ответ.
MAX_STATS_ATTEMPTS = 3


async def connect(dsn: str) -> asyncpg.Pool:
    """Пул соединений. На 2 ГБ RAM хватает 1–5 соединений.

    min_size=0: пул не держит «пол» открытых соединений и не пытается
    переподключаться в фоне, если база временно недоступна (например, рвётся
    SSH-туннель при переключении сети). Ошибки в лог не сыплются, а функции,
    которым нужна база, просто получат исключение.
    """
    return await asyncpg.create_pool(dsn=dsn, min_size=0, max_size=5, command_timeout=30)


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


async def finished_matches(
    pool: asyncpg.Pool, competition_id: str | None = None
) -> list[asyncpg.Record]:
    """Завершённые матчи со счётом — материал для модели."""
    async with pool.acquire() as conn:
        if competition_id:
            return await conn.fetch(
                """
                SELECT kickoff_utc, home_team, away_team, home_goals, away_goals,
                       home_goals_ht, away_goals_ht, home_corners, away_corners,
                       home_yellow, away_yellow, competition_id
                  FROM matches
                 WHERE status = 'finished'
                   AND home_goals IS NOT NULL
                   AND away_goals IS NOT NULL
                   AND competition_id = $1
                 ORDER BY kickoff_utc
                """,
                competition_id,
            )
        return await conn.fetch(
            """
            SELECT kickoff_utc, home_team, away_team, home_goals, away_goals,
                   home_goals_ht, away_goals_ht, home_corners, away_corners,
                   home_yellow, away_yellow, competition_id
              FROM matches
             WHERE status = 'finished'
               AND home_goals IS NOT NULL
               AND away_goals IS NOT NULL
             ORDER BY kickoff_utc
            """
        )


async def upcoming_matches(pool: asyncpg.Pool, days: int) -> list[asyncpg.Record]:
    """Запланированные матчи на ближайшие дни (с названием соревнования)."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT m.id, m.competition_id, c.name_ru, m.home_team, m.away_team, m.kickoff_utc
              FROM matches m
              JOIN competitions c ON c.id = m.competition_id
             WHERE m.status = 'scheduled'
               AND m.kickoff_utc >= now()
               AND m.kickoff_utc <= now() + $1
             ORDER BY m.kickoff_utc
            """,
            timedelta(days=days),
        )


async def teams_without_venue(pool: asyncpg.Pool, limit: int) -> list[asyncpg.Record]:
    """Команды, для которых ещё не известен город стадиона."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT DISTINCT ON (m.home_team)
                   m.home_team AS team, m.source_game_id, c.source_key AS slug
              FROM matches m
              JOIN competitions c ON c.id = m.competition_id
              LEFT JOIN venues v ON v.team = m.home_team
             WHERE v.team IS NULL
               AND m.source_game_id IS NOT NULL
               AND m.status = 'finished'
             ORDER BY m.home_team, m.kickoff_utc DESC
             LIMIT $1
            """,
            limit,
        )


async def upsert_venue(
    pool: asyncpg.Pool,
    team: str,
    city: str | None,
    country: str | None,
    lat: float | None = None,
    lon: float | None = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO venues (team, city, country, lat, lon, updated_at)
            VALUES ($1, $2, $3, $4, $5, now())
            ON CONFLICT (team) DO UPDATE SET
                city = COALESCE(EXCLUDED.city, venues.city),
                country = COALESCE(EXCLUDED.country, venues.country),
                lat = COALESCE(EXCLUDED.lat, venues.lat),
                lon = COALESCE(EXCLUDED.lon, venues.lon),
                updated_at = now()
            """,
            team,
            city,
            country,
            lat,
            lon,
        )


async def venue_coords(pool: asyncpg.Pool, team: str) -> tuple[float, float] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT lat, lon FROM venues WHERE team = $1 AND lat IS NOT NULL", team
        )
    if row is None:
        return None
    return float(row["lat"]), float(row["lon"])


async def competition_ids(pool: asyncpg.Pool) -> list[str]:
    """Список соревнований, по которым есть матчи со счётом."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT competition_id, count(*) AS matches
              FROM matches
             WHERE status = 'finished' AND home_goals IS NOT NULL
             GROUP BY competition_id
            HAVING count(*) >= 100
             ORDER BY count(*) DESC
            """
        )
    return [row["competition_id"] for row in rows]


async def due_matches(pool: asyncpg.Pool, lead_hours: int) -> list[asyncpg.Record]:
    """Матчи, до которых осталось не больше lead_hours и которые ещё не начались."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT m.id, m.competition_id, c.name_ru, m.home_team, m.away_team,
                   m.kickoff_utc
              FROM matches m
              JOIN competitions c ON c.id = m.competition_id
             WHERE m.status = 'scheduled'
               AND m.kickoff_utc > now()
               AND m.kickoff_utc <= now() + $1
             ORDER BY m.kickoff_utc
            """,
            timedelta(hours=lead_hours),
        )


async def active_subscribers(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Подписчики с действующей подпиской."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT chat_id, username
              FROM subscribers
             WHERE is_active
               AND paid_until IS NOT NULL
               AND paid_until > now()
             ORDER BY chat_id
            """
        )


async def save_prediction(
    pool: asyncpg.Pool, match_id: int, model_version: str, payload: str
) -> None:
    """Сохранить прогноз (пересчёт того же матча перезаписывает запись)."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO predictions (match_id, model_version, payload)
            VALUES ($1, $2, $3::jsonb)
            ON CONFLICT (match_id, model_version) DO UPDATE SET
                payload = EXCLUDED.payload,
                created_at = now()
            """,
            match_id,
            model_version,
            payload,
        )


async def try_insert_delivery(
    pool: asyncpg.Pool, match_id: int, chat_id: int, status: str, error: str | None = None
) -> bool:
    """Зафиксировать отправку. False — уже было (значит, повторять не надо)."""
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO deliveries (match_id, chat_id, status, error)
                VALUES ($1, $2, $3, $4)
                """,
                match_id,
                chat_id,
                status,
                error,
            )
        except asyncpg.UniqueViolationError:
            return False
    return True


async def delivery_stats(pool: asyncpg.Pool) -> dict[str, int]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT count(*) FILTER (WHERE status = 'sent') AS sent,
                   count(*) FILTER (WHERE status = 'dry_run') AS dry_run,
                   count(*) FILTER (WHERE status = 'failed') AS failed
              FROM deliveries
            """
        )
    return {key: int(row[key] or 0) for key in ("sent", "dry_run", "failed")}


async def latest_prediction(pool: asyncpg.Pool) -> str | None:
    """Текстовый прогноз по ближайшему матчу — для кнопки «Прогноз на сегодня»."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT m.home_team, m.away_team, m.kickoff_utc
              FROM predictions p
              JOIN matches m ON m.id = p.match_id
             WHERE m.kickoff_utc > now()
             ORDER BY m.kickoff_utc
             LIMIT 1
            """
        )
    if row is None:
        return None
    return "%s — %s\\n%s UTC" % (
        row["home_team"],
        row["away_team"],
        row["kickoff_utc"].strftime("%d.%m %H:%M"),
    )


async def has_active_subscription(pool: asyncpg.Pool, chat_id: int) -> bool:
    """Есть ли у пользователя действующая подписка."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 1 FROM subscribers
             WHERE chat_id = $1
               AND is_active
               AND paid_until IS NOT NULL
               AND paid_until > now()
            """,
            chat_id,
        )
    return row is not None


# Версия текста согласия. Растёт, если меняется формулировка, и тогда согласие
# запрашивается заново — старое согласие на новый текст не распространяется.
CONSENT_VERSION = "1.0"


async def has_consent(pool: asyncpg.Pool, chat_id: int) -> bool:
    """Дал ли пользователь согласие на текущую редакцию."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT consent_version FROM subscribers WHERE chat_id = $1", chat_id
        )
    return bool(row and row["consent_version"] == CONSENT_VERSION)


async def record_consent(pool: asyncpg.Pool, chat_id: int, username: str | None) -> None:
    """Зафиксировать согласие (создаёт запись, если пользователь новый)."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO subscribers (chat_id, username, consent_version, consent_at, age_confirmed)
            VALUES ($1, $2, $3, now(), TRUE)
            ON CONFLICT (chat_id) DO UPDATE SET
                username = COALESCE(EXCLUDED.username, subscribers.username),
                consent_version = EXCLUDED.consent_version,
                consent_at = now(),
                age_confirmed = TRUE,
                updated_at = now()
            """,
            chat_id,
            username,
            CONSENT_VERSION,
        )


async def subscription_until(pool: asyncpg.Pool, chat_id: int) -> datetime | None:
    """До какого срока действует подписка (None — подписки нет)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT paid_until FROM subscribers WHERE chat_id = $1", chat_id)
    return row["paid_until"] if row else None
