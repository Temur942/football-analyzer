"""Командная строка проекта: ручные проходы и проверки.

Запуск:
    python -m football_analyzer.cli fetch-schedule --days 3
    python -m football_analyzer.cli competitions

Здесь нет ничего платного: только бесплатные источники расписания.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from football_analyzer.competitions import active_competitions
from football_analyzer.config import Settings, get_settings
from football_analyzer.datasources.espn import ScheduleFetch, fetch_schedule
from football_analyzer.models import Competition
from football_analyzer.storage import db

EXIT_OK = 0
EXIT_PROBLEM = 1
EXIT_EMPTY = 2


def cmd_competitions(args: argparse.Namespace) -> int:
    competitions = active_competitions()
    if args.json:
        print(json.dumps([c.model_dump() for c in competitions], ensure_ascii=False, indent=2))
        return EXIT_OK
    print("=" * 74)
    print("  СОРЕВНОВАНИЯ В РАБОТЕ: %d" % len(competitions))
    print("=" * 74)
    for competition in competitions:
        print(
            "  %-34s %-18s ESPN: %s"
            % (competition.name_ru[:34], competition.country[:18], competition.espn or "—")
        )
    print("=" * 74)
    return EXIT_OK


def cmd_fetch_schedule(args: argparse.Namespace) -> int:
    settings = get_settings()
    competitions = active_competitions()
    if args.competition:
        wanted = set(args.competition)
        competitions = [c for c in competitions if c.id in wanted]
        missing = wanted - {c.id for c in competitions}
        if missing:
            print(
                "ОШИБКА: неизвестные соревнования: %s" % ", ".join(sorted(missing)), file=sys.stderr
            )
            return EXIT_PROBLEM

    result = asyncio.run(
        fetch_schedule(
            competitions,
            days_ahead=args.days,
            days_back=args.back,
            timeout_seconds=settings.http_timeout_seconds,
        )
    )

    if args.json:
        print(
            json.dumps(
                {
                    "fixtures": [
                        {
                            **fixture.model_dump(mode="json"),
                        }
                        for fixture in result.fixtures
                    ],
                    "requests": result.requests,
                    "errors": result.errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print("=" * 96)
        print(
            "  РАСПИСАНИЕ: %d матчей, %d запросов к источнику"
            % (len(result.fixtures), result.requests)
        )
        print("=" * 96)
        for fixture in result.fixtures:
            print(
                "  %s UTC  %-30s %-28s — %-28s %s"
                % (
                    fixture.kickoff_utc.strftime("%Y-%m-%d %H:%M"),
                    fixture.competition_id[:30],
                    fixture.home_team[:28],
                    fixture.away_team[:28],
                    fixture.status,
                )
            )
        print("=" * 96)
        if result.errors:
            print("  НЕ ПОЛУЧЕНО (%d):" % len(result.errors))
            for error in result.errors[:20]:
                print("    %s" % error)
            if len(result.errors) > 20:
                print("    ... ещё %d" % (len(result.errors) - 20))
            print("=" * 96)

    if result.errors and not result.fixtures:
        return EXIT_PROBLEM
    if not result.fixtures:
        return EXIT_EMPTY
    if result.errors:
        return EXIT_PROBLEM
    return EXIT_OK


async def _migrate(dsn: str) -> list[str]:
    pool = await db.connect(dsn)
    try:
        await db.apply_schema(pool)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
            )
        return [row["tablename"] for row in rows]
    finally:
        await pool.close()


def cmd_migrate(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        tables = asyncio.run(_migrate(settings.require_database_url()))
    except Exception as exc:  # noqa: BLE001
        print("ОШИБКА миграции: %s" % exc, file=sys.stderr)
        return EXIT_PROBLEM
    print("=" * 70)
    print("  СХЕМА ПРИМЕНЕНА (idempotent, повторный запуск безопасен)")
    print("=" * 70)
    for table in tables:
        print("  таблица: %s" % table)
    print("=" * 70)
    return EXIT_OK


async def _sync_schedule(
    settings: Settings, competitions: list[Competition], args: argparse.Namespace
) -> tuple[ScheduleFetch, int, int, int]:
    fetch_result = await fetch_schedule(
        competitions,
        days_ahead=args.days,
        days_back=args.back,
        timeout_seconds=settings.http_timeout_seconds,
    )
    pool = await db.connect(settings.require_database_url())
    try:
        await db.apply_schema(pool)
        await db.upsert_competitions(pool, competitions)
        run_id = await db.start_ingest_run(pool, "ESPN", None)
        new_count, updated_count = await db.upsert_fixtures(pool, fetch_result.fixtures)
        await db.finish_ingest_run(
            pool,
            run_id,
            status="ok" if not fetch_result.errors else "partial",
            seen=len(fetch_result.fixtures),
            new_count=new_count,
            updated_count=updated_count,
            error="; ".join(fetch_result.errors[:5]) or None,
        )
        total = await db.count_matches(pool, args.days)
    finally:
        await pool.close()
    return fetch_result, new_count, updated_count, total


def cmd_sync_schedule(args: argparse.Namespace) -> int:
    """Собрать расписание и записать в PostgreSQL."""
    settings = get_settings()
    competitions = active_competitions()
    if args.competition:
        wanted = set(args.competition)
        competitions = [c for c in competitions if c.id in wanted]
        missing = wanted - {c.id for c in competitions}
        if missing:
            print(
                "ОШИБКА: неизвестные соревнования: %s" % ", ".join(sorted(missing)),
                file=sys.stderr,
            )
            return EXIT_PROBLEM

    try:
        fetch_result, new_count, updated_count, total = asyncio.run(
            _sync_schedule(settings, competitions, args)
        )
    except Exception as exc:  # noqa: BLE001
        print("ОШИБКА записи в базу: %s" % exc, file=sys.stderr)
        return EXIT_PROBLEM

    print("=" * 78)
    print("  СИНХРОНИЗАЦИЯ РАСПИСАНИЯ → PostgreSQL")
    print("=" * 78)
    print(
        "  Получено матчей:        %d (запросов: %d)"
        % (len(fetch_result.fixtures), fetch_result.requests)
    )
    print("  Новых в базе:           %d" % new_count)
    print("  Обновлено:              %d" % updated_count)
    print("  Матчей в базе на окно:  %d (сейчас..+%d дней)" % (total, args.days))
    if fetch_result.errors:
        print("  Ошибок источника:       %d" % len(fetch_result.errors))
        for error in fetch_result.errors[:10]:
            print("    %s" % error)
    print("=" * 78)
    print("  Доказательство: строка в таблице matches, а не лог.")
    if fetch_result.errors and not fetch_result.fixtures:
        return EXIT_PROBLEM
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="football-analyzer", description="Служебные команды")
    parser.add_argument("--log-level", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    p_competitions = sub.add_parser("competitions", help="список соревнований")
    p_competitions.add_argument("--json", action="store_true")
    p_competitions.set_defaults(func=cmd_competitions)

    p_schedule = sub.add_parser("fetch-schedule", help="получить расписание на окно дат")
    p_schedule.add_argument("--days", type=int, default=3, help="сколько дней вперёд")
    p_schedule.add_argument("--back", type=int, default=0, help="сколько дней назад")
    p_schedule.add_argument(
        "--competition",
        action="append",
        default=None,
        help="только это соревнование (можно несколько)",
    )
    p_schedule.add_argument("--json", action="store_true")
    p_schedule.set_defaults(func=cmd_fetch_schedule)

    p_migrate = sub.add_parser("migrate", help="применить схему БД (idempotent)")
    p_migrate.set_defaults(func=cmd_migrate)

    p_sync = sub.add_parser("sync-schedule", help="собрать расписание и записать в PostgreSQL")
    p_sync.add_argument("--days", type=int, default=3, help="сколько дней вперёд")
    p_sync.add_argument("--back", type=int, default=0, help="сколько дней назад")
    p_sync.add_argument(
        "--competition",
        action="append",
        default=None,
        help="только это соревнование (можно несколько)",
    )
    p_sync.set_defaults(func=cmd_sync_schedule)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, (args.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
