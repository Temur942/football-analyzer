"""Поиск прошедших матчей за сезон из ESPN (история для модели).

Как работает: ESPN отдаёт календарь соревнования — список дат, в которые
бывают матчи. По каждой дате запрашиваем scoreboard и забираем матчи.
Это ~85 запросов на лигу-сезон вместо перебора 365 дней.

Ограничение: у кубков и турниров сборных календарь приходит диапазоном без
перечня дат — для них нужен перебор дней, он вынесен в отдельный проход.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import aiohttp

from football_analyzer.datasources.espn import (
    fetch_calendar,
    fetch_scoreboard,
    parse_event,
)
from football_analyzer.models import Competition, Fixture

log = logging.getLogger(__name__)


@dataclass
class DiscoveryResult:
    fixtures: list[Fixture] = field(default_factory=list)
    requests: int = 0
    errors: list[str] = field(default_factory=list)
    skipped: str | None = None


async def discover_season(
    session: aiohttp.ClientSession,
    competition: Competition,
    season: str,
    *,
    pause_seconds: float = 0.3,
) -> DiscoveryResult:
    """Собрать матчи соревнования за сезон (например, "2024-2025")."""
    result = DiscoveryResult()
    if not competition.espn:
        result.skipped = "нет ключа ESPN"
        return result
    start_year = int(season.split("-")[0])

    try:
        calendar, _name = await fetch_calendar(session, competition.espn, start_year)
    except Exception as exc:  # noqa: BLE001
        result.errors.append("%s %s: календарь — %s" % (competition.id, season, exc))
        result.requests += 1
        return result
    result.requests += 1

    if calendar is None:
        result.skipped = "календарь-диапазон (кубок/сборные) — нужен перебор дат"
        return result

    for day in sorted(calendar):
        try:
            events = await fetch_scoreboard(session, competition.espn, day)
        except Exception as exc:  # noqa: BLE001
            result.errors.append("%s %s за %s: %s" % (competition.id, season, day, exc))
            continue
        finally:
            result.requests += 1
        for event in events:
            fixture = parse_event(event, competition)
            if fixture is not None:
                result.fixtures.append(fixture)
        await asyncio.sleep(pause_seconds)

    return result
