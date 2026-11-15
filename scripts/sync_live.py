"""Live-табло: счёт и минута матчей в реальном времени.

ESPN отдаёт статус матча («in», минута), счёт и события голов бесплатно.
Раз в минуту собираем состояние и пишем в live.json — страница /live/
перечитывает его сама, без перезапуска сервера.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiohttp

from football_analyzer.competitions import load_competitions
from football_analyzer.teams_ru import to_russian

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "landing" / "live.json"
API = "https://site.api.espn.com/apis/site/v2/sports/soccer/%s/scoreboard"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/soccer/%s/summary"


def status_label(state: str, detail: str, clock: str | None) -> str:
    if state == "in":
        return clock or "идёт"
    if state == "post":
        return "завершён"
    return "не начался"


async def fetch_day(session: aiohttp.ClientSession, slug: str, day: str) -> list[dict]:
    url = API % slug
    try:
        async with session.get(url, params={"dates": day}) as response:
            if response.status != 200:
                return []
            data = await response.json(content_type=None)
    except Exception:  # noqa: BLE001
        return []
    return data.get("events", []) or []


EVENT_ICONS = {
    "Goal": "⚽",
    "Penalty - Scored": "⚽",
    "Own Goal": "⚽",
    "Yellow Card": "🟨",
    "Red Card": "🟥",
    "Second Yellow Card": "🟥",
}


async def fetch_events(session: aiohttp.ClientSession, slug: str, event_id: str) -> list[dict]:
    """Голы и карточки матча: кто и на какой минуте."""
    try:
        async with session.get(SUMMARY % slug, params={"event": event_id}) as response:
            if response.status != 200:
                return []
            data = await response.json(content_type=None)
    except Exception:  # noqa: BLE001
        return []

    result: list[dict] = []
    for event in data.get("keyEvents") or []:
        label = (event.get("type") or {}).get("text", "")
        icon = EVENT_ICONS.get(label)
        if icon is None:
            continue
        athlete = ""
        participants = event.get("participants") or []
        if participants:
            athlete = (participants[0].get("athlete") or {}).get("displayName", "")
        team = (event.get("team") or {}).get("displayName", "")
        result.append(
            {
                "icon": icon,
                "type": label,
                "minute": (event.get("clock") or {}).get("displayValue", ""),
                "player": athlete,
                "team": to_russian(team) if team else "",
            }
        )
    return result


async def main() -> None:
    competitions = load_competitions()
    today = datetime.now(UTC)
    days = [
        (today - timedelta(days=1)).strftime("%Y%m%d"),
        today.strftime("%Y%m%d"),
    ]

    matches: list[dict] = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        for competition in competitions:
            slug = getattr(competition, "espn", None)
            if not slug:
                continue
            for day in days:
                day_events = await fetch_day(session, slug, day)
                for event in day_events:
                    competition_block = (event.get("competitions") or [{}])[0]
                    status = competition_block.get("status") or {}
                    state = (status.get("type") or {}).get("state", "pre")
                    competitors = competition_block.get("competitors") or []
                    if len(competitors) != 2:
                        continue
                    sides = {}
                    for side in competitors:
                        sides[side.get("homeAway", "home")] = side
                    home = sides.get("home")
                    away = sides.get("away")
                    if not home or not away:
                        continue
                    events: list[dict] = []
                    recent = False
                    kickoff_text = event.get("date") or ""
                    try:
                        kickoff_dt = datetime.fromisoformat(kickoff_text.replace("Z", "+00:00"))
                        recent = datetime.now(UTC) - kickoff_dt < timedelta(hours=4)
                    except ValueError:
                        recent = False
                    if state == "in" or (state == "post" and recent):
                        events = await fetch_events(session, slug, str(event.get("id")))
                    matches.append(
                        {
                            "id": event.get("id"),
                            "events": events,
                            "competition": getattr(competition, "name_ru", slug),
                            "home": to_russian(home["team"]["displayName"]),
                            "away": to_russian(away["team"]["displayName"]),
                            "home_score": home.get("score"),
                            "away_score": away.get("score"),
                            "state": state,
                            "detail": (status.get("type") or {}).get("detail", ""),
                            "clock": status.get("displayClock"),
                            "label": status_label(
                                state,
                                (status.get("type") or {}).get("detail", ""),
                                status.get("displayClock"),
                            ),
                            "kickoff": event.get("date"),
                        }
                    )

    # Сначала идущие, потом ближайшие, потом завершённые
    order = {"in": 0, "pre": 1, "post": 2}
    matches.sort(key=lambda item: (order.get(item["state"], 3), item.get("kickoff") or ""))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "matches": matches[:120],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    live = sum(1 for match in matches if match["state"] == "in")
    print("live.json: %d матчей, из них идут %d" % (len(matches), live))


if __name__ == "__main__":
    asyncio.run(main())
