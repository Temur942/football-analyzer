"""Статистика завершённого матча из ESPN summary.

Откуда данные: `GET /apis/site/v2/sports/soccer/{slug}/summary?event={id}`.
Проверено 06.11.2026: эндпоинт отдаёт по каждой команде углы (`wonCorners`),
жёлтые (`yellowCards`) и красные (`redCards`) карточки, а `linescores` — счёт
по таймам. Браузер и ключ не нужны, платы нет.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp

from football_analyzer.datasources.espn import ESPN_API, USER_AGENT

log = logging.getLogger(__name__)


@dataclass
class MatchStats:
    """Числа одного завершённого матча.

    None означает «источник не дал значение», а не ноль: ноль углов —
    это реальный результат, и путать его с отсутствием данных нельзя.
    """

    event_id: str
    home_goals: int | None = None
    away_goals: int | None = None
    home_goals_ht: int | None = None
    away_goals_ht: int | None = None
    home_corners: int | None = None
    away_corners: int | None = None
    home_yellow: int | None = None
    away_yellow: int | None = None
    home_red: int | None = None
    away_red: int | None = None

    @property
    def has_scores(self) -> bool:
        return self.home_goals is not None and self.away_goals is not None

    @property
    def has_corners_and_cards(self) -> bool:
        return None not in (
            self.home_corners,
            self.away_corners,
            self.home_yellow,
            self.away_yellow,
        )


def to_int(value: object) -> int | None:
    """ESPN отдаёт числа строками ('8') и иногда пустыми строками."""
    if value is None or value == "":
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _half_score(competitor: dict, index: int) -> int | None:
    linescores = competitor.get("linescores") or []
    if len(linescores) <= index:
        return None
    entry = linescores[index]
    if isinstance(entry, dict):
        return to_int(entry.get("displayValue"))
    return to_int(entry)


def _team_stats(team_block: dict) -> dict[str, int | None]:
    stats: dict[str, int | None] = {}
    for item in team_block.get("statistics") or []:
        name = item.get("name")
        if name:
            stats[name] = to_int(item.get("displayValue"))
    return stats


def parse_summary(summary: dict, event_id: str) -> MatchStats | None:
    """Разобрать ответ summary. None — если это не матч с двумя командами."""
    header = summary.get("header") or {}
    competitions = header.get("competitions") or []
    if not competitions:
        return None
    competitors = competitions[0].get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if home is None or away is None:
        return None

    stats = MatchStats(event_id=event_id)
    stats.home_goals = to_int(home.get("score"))
    stats.away_goals = to_int(away.get("score"))
    stats.home_goals_ht = _half_score(home, 0)
    stats.away_goals_ht = _half_score(away, 0)

    teams = (summary.get("boxscore") or {}).get("teams") or []
    by_side: dict[str, dict[str, int | None]] = {}
    for team in teams:
        side = (team.get("homeAway") or "").lower()
        if side:
            by_side[side] = _team_stats(team)

    home_stats = by_side.get("home") or {}
    away_stats = by_side.get("away") or {}
    stats.home_corners = home_stats.get("wonCorners")
    stats.away_corners = away_stats.get("wonCorners")
    stats.home_yellow = home_stats.get("yellowCards")
    stats.away_yellow = away_stats.get("yellowCards")
    stats.home_red = home_stats.get("redCards")
    stats.away_red = away_stats.get("redCards")
    return stats


def has_any_statistics(summary: dict) -> bool:
    """Есть ли в ответе хоть какие-то статистики команд.

    Нужен, чтобы отличить «источник не хранит подробности» (boxscore пустой)
    от «нам не хватило одного показателя»: в первом случае повторять бессмысленно.
    """
    teams = (summary.get("boxscore") or {}).get("teams") or []
    return any(team.get("statistics") for team in teams)


async def fetch_summary(session: aiohttp.ClientSession, slug: str, event_id: str) -> dict:
    """Сырой ответ summary по матчу."""
    url = "%s/%s/summary?event=%s" % (ESPN_API, slug, event_id)
    async with session.get(url, headers={"User-Agent": USER_AGENT}) as response:
        if response.status != 200:
            raise RuntimeError("HTTP %d на %s" % (response.status, url))
        return await response.json(content_type=None)


def parse_venue(summary: dict) -> tuple[str | None, str | None]:
    """Город и страна стадиона из gameInfo (координат ESPN не даёт)."""
    venue = (summary.get("gameInfo") or {}).get("venue") or {}
    address = venue.get("address") or {}
    city = address.get("city")
    country = address.get("country")
    return (str(city) if city else None, str(country) if country else None)


def parse_officials(summary: dict) -> str | None:
    """Имя главного арбитра из gameInfo.officials (order=1 — главный)."""
    officials = (summary.get("gameInfo") or {}).get("officials") or []
    for official in officials:
        if int(official.get("order") or 99) == 1:
            name = official.get("displayName") or official.get("fullName")
            return str(name) if name else None
    return None
