"""Расписание матчей из открытого API ESPN.

Почему не soccerdata.ESPN.read_schedule(): он выгружает ВЕСЬ сезон — по
календарю соревнования это ~80 дат на лигу, то есть около 1600 запросов на
20 соревнований за один проход. Боту 24/7 нужны только ближайшие дни, поэтому
запрашиваем узкое окно по тому же самому эндпоинту, который использует
soccerdata. Формат ответа и правила — те же.

Деньги: источник бесплатный, платных вызовов здесь нет. Но есть лимиты
вежливости — между запросами выдерживается пауза, ошибки не роняют проход.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import aiohttp

from football_analyzer.models import Competition, Fixture, season_of

log = logging.getLogger(__name__)

ESPN_API = "https://site.api.espn.com/apis/site/v2/sports/soccer"
USER_AGENT = "football-analyzer/0.1 (+contact: project owner)"
STATUS_MAP = {"pre": "scheduled", "in": "live", "post": "finished"}


@dataclass
class ScheduleFetch:
    """Результат прохода: матчи и честный список того, что не получилось."""

    fixtures: list[Fixture] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    requests: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_event(event: dict, competition: Competition) -> Fixture | None:
    """Преобразовать событие ESPN в матч. None — если данных не хватает."""
    try:
        kickoff = datetime.fromisoformat(str(event["date"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=UTC)

    competitions = event.get("competitions") or []
    if not competitions:
        return None
    competitors = competitions[0].get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if home is None or away is None:
        return None

    def team_name(side: dict) -> str:
        team = side.get("team") or {}
        return team.get("displayName") or team.get("name") or ""

    home_name, away_name = team_name(home), team_name(away)
    if not home_name or not away_name:
        return None

    state = ((event.get("status") or {}).get("type") or {}).get("state", "pre")

    return Fixture(
        competition_id=competition.id,
        season=season_of(kickoff),
        kickoff_utc=kickoff.astimezone(UTC),
        home_team=home_name,
        away_team=away_name,
        source="ESPN",
        source_game_id=str(event.get("id")) if event.get("id") is not None else None,
        status=STATUS_MAP.get(state, "scheduled"),
    )


async def fetch_scoreboard(session: aiohttp.ClientSession, slug: str, day: str) -> list[dict]:
    """Матчи одного соревнования за одну дату (YYYYMMDD)."""
    url = "%s/%s/scoreboard?dates=%s" % (ESPN_API, slug, day)
    async with session.get(url) as response:
        if response.status != 200:
            raise RuntimeError("HTTP %d на %s" % (response.status, url))
        data = await response.json(content_type=None)
    return data.get("events") or []


async def fetch_calendar(
    session: aiohttp.ClientSession, slug: str, season_year: int
) -> tuple[set[str] | None, str]:
    """Календарь соревнования: даты, в которые бывают матчи.

    Возвращает (даты или None, имя соревнования).

    ESPN отдаёт календарь в двух видах:
      * лига — список дат: тогда известны все игровые дни, и по остальным
        датам можно вообще не ходить (экономия запросов), а пустой ответ
        в игровой день — признак сбоя источника, его надо повторить;
      * кубок и сборные — только диапазон {startDate, endDate}: конкретных
        дат нет, пустой ответ может быть нормой.
    """
    url = "%s/%s/scoreboard?dates=%d0701" % (ESPN_API, slug, season_year)
    async with session.get(url) as response:
        if response.status != 200:
            raise RuntimeError("HTTP %d на %s" % (response.status, url))
        data = await response.json(content_type=None)
    leagues = data.get("leagues") or []
    if not leagues:
        return None, ""
    name = leagues[0].get("name") or ""
    calendar = leagues[0].get("calendar") or []
    plain = [d for d in calendar if isinstance(d, str)]
    if not plain:
        return None, name
    return {d[:10].replace("-", "") for d in plain}, name


async def fetch_schedule(
    competitions: list[Competition],
    *,
    days_ahead: int = 3,
    days_back: int = 0,
    timeout_seconds: int = 15,
    pause_seconds: float = 0.25,
) -> ScheduleFetch:
    """Расписание на окно дат: сегодня − days_back .. сегодня + days_ahead (UTC).

    Ошибка одного соревнования или даты не прерывает проход: она попадает в
    result.errors, а остальные данные собираются.
    """
    today = datetime.now(tz=UTC).date()
    days = [
        (today + timedelta(days=offset)).strftime("%Y%m%d")
        for offset in range(-days_back, days_ahead + 1)
    ]

    result = ScheduleFetch()
    seen: set[str] = set()
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    headers = {"User-Agent": USER_AGENT}

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        for competition in competitions:
            if not competition.espn:
                result.errors.append("%s: нет ключа ESPN" % competition.id)
                continue

            # Календарь: в лигах он говорит, в какие дни вообще бывают матчи.
            # Сезон начинается в июле: в январе–июне актуален прошлый год.
            season_year = today.year if today.month >= 7 else today.year - 1
            calendar: set[str] | None = None
            try:
                calendar, _name = await fetch_calendar(session, competition.espn, season_year)
            except Exception as exc:  # noqa: BLE001
                log.warning("календарь %s не получен: %s", competition.id, exc)
            finally:
                result.requests += 1
            await asyncio.sleep(pause_seconds)

            for day in days:
                if calendar is not None and day not in calendar:
                    # В этот день матчей этого соревнования не бывает — не ходим.
                    continue

                # В игровой день пустой ответ означает сбой источника
                # (HTTP 200 и пустой список — так ESPN отдаёт при троттлинге),
                # поэтому повторяем. Иначе бот молча теряет матч.
                expected = calendar is not None
                events: list[dict] = []
                for attempt in range(3 if expected else 1):
                    try:
                        events = await fetch_scoreboard(session, competition.espn, day)
                    except Exception as exc:  # noqa: BLE001
                        message = "%s (%s) за %s: %s" % (
                            competition.id,
                            competition.espn,
                            day,
                            exc,
                        )
                        log.warning("расписание не получено — %s", message)
                        result.errors.append(message)
                        events = []
                        await asyncio.sleep(pause_seconds * (attempt + 1))
                        continue
                    finally:
                        result.requests += 1
                    if events:
                        break
                    await asyncio.sleep(pause_seconds * (attempt + 1))

                if expected and not events:
                    # Календарь говорит, что матчи в этот день есть, но источник
                    # их не отдал даже после повторов. Молчать нельзя: это
                    # потерянный матч, а не «выходной».
                    message = "%s (%s): игровой день %s без матчей после повторов" % (
                        competition.id,
                        competition.espn,
                        day,
                    )
                    log.error("возможная потеря матчей — %s", message)
                    result.errors.append(message)

                for event in events:
                    fixture = parse_event(event, competition)
                    if fixture is None:
                        continue
                    if fixture.dedupe_key in seen:
                        continue
                    seen.add(fixture.dedupe_key)
                    result.fixtures.append(fixture)
                await asyncio.sleep(pause_seconds)

    result.fixtures.sort(key=lambda f: (f.kickoff_utc, f.competition_id))
    return result
