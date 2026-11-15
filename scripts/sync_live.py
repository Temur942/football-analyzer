"""Live-табло: счёт и минута матчей в реальном времени.

ESPN отдаёт статус матча («in», минута), счёт и события голов бесплатно.
Раз в минуту собираем состояние и пишем в live.json — страница /live/
перечитывает его сама, без перезапуска сервера.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiohttp

from football_analyzer.competitions import load_competitions
from football_analyzer.teams_ru import to_russian

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "landing" / "live.json"
API = "https://site.api.espn.com/apis/site/v2/sports/soccer/%s/scoreboard"


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
                for event in await fetch_day(session, slug, day):
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
                    matches.append(
                        {
                            "id": event.get("id"),
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
