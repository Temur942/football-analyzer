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
from football_analyzer.config import get_settings
from football_analyzer.datasources.espn import fetch_schedule

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
