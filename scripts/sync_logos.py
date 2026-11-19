"""Эмблемы клубов: скачать логотипы ESPN и раздать их страницам.

Зачем: в таблицах и расписаниях без эмблем читать неудобно — человек ищет
команду глазами, а не по названию.

Почему локально, а не ссылкой на ESPN: чужой CDN может закрыть доступ или
поменять пути, а тогда половина страниц останется без картинок. Скачиваем
один раз и раздаём со своего сервера.

Правовая оговорка: эмблемы — товарные знаки клубов. Использование для
идентификации команды в информационном сервисе — распространённая практика,
но правовая чистота не гарантирована. Если нужно полностью убрать риск,
достаточно очистить каталог logos и флаг отрисовки в шаблонах.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp

from football_analyzer.competitions import load_competitions

ROOT = Path(__file__).resolve().parent.parent
LOGO_DIR = ROOT / "docs" / "landing" / "logos"
MAP_FILE = LOGO_DIR / "teams.json"
API = "https://site.api.espn.com/apis/site/v2/sports/soccer/%s/scoreboard"
STANDINGS = "https://site.api.espn.com/apis/v2/sports/soccer/%s/standings"
CDN = "https://a.espncdn.com/i/teamlogos/soccer/500/%s.png"


async def collect_standings(session: aiohttp.ClientSession, slug: str) -> dict[str, dict]:
    """Все команды турнира из таблицы — там у каждой есть эмблема."""
    teams: dict[str, dict] = {}
    try:
        async with session.get(STANDINGS % slug) as response:
            if response.status != 200:
                return {}
            data = await response.json(content_type=None)
    except Exception:  # noqa: BLE001
        return {}

    def walk(node: dict) -> None:
        for entry in (node.get("standings") or {}).get("entries") or []:
            team = entry.get("team") or {}
            name = team.get("displayName")
            team_id = team.get("id")
            logos = team.get("logos") or []
            if name and team_id:
                teams.setdefault(
                    name,
                    {
                        "id": team_id,
                        "color": team.get("color"),
                        "logo": logos[0].get("href") if logos else None,
                    },
                )
        for child in node.get("children") or []:
            walk(child)

    walk(data)
    return teams


async def collect(session: aiohttp.ClientSession, slug: str) -> dict[str, dict]:
    """Собрать команды: имя → id и цвет. Ищем по расписанию за два месяца."""
    teams: dict[str, dict] = {}
    today = datetime.utcnow()
    for weeks_ago in range(0, 17):
        day = today - timedelta(days=weeks_ago * 7)
        try:
            async with session.get(
                API % slug, params={"dates": day.strftime("%Y%m%d")}
            ) as response:
                if response.status != 200:
                    continue
                data = await response.json(content_type=None)
        except Exception:  # noqa: BLE001
            continue
        for event in data.get("events") or []:
            for competition in event.get("competitions") or []:
                for side in competition.get("competitors") or []:
                    team = side.get("team") or {}
                    name = team.get("displayName")
                    team_id = team.get("id")
                    if name and team_id:
                        teams[name] = {
                            "id": team_id,
                            "color": team.get("color"),
                            "logo": team.get("logo"),
                        }
    return teams


async def main() -> None:
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    competitions = load_competitions()
    mapping: dict[str, dict] = {}

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as session:
        for competition in competitions:
            slug = getattr(competition, "espn", None)
            if not slug:
                continue
            teams = await collect_standings(session, slug)
            if not teams:
                teams = await collect(session, slug)
            mapping.update(teams)
            print("%-22s команд: %d" % (slug, len(teams)))

        downloaded = skipped = failed = 0
        for info in mapping.values():
            path = LOGO_DIR / ("%s.png" % info["id"])
            if path.exists() and path.stat().st_size > 500:
                skipped += 1
                info["file"] = "/logos/%s.png" % info["id"]
                continue
            source = info.get("logo") or (CDN % info["id"])
            try:
                async with session.get(source) as response:
                    if response.status != 200:
                        failed += 1
                        continue
                    data = await response.read()
            except Exception:  # noqa: BLE001
                failed += 1
                continue
            if len(data) < 500:
                failed += 1
                continue
            path.write_bytes(data)
            info["file"] = "/logos/%s.png" % info["id"]
            downloaded += 1

    clean = {
        name: {"file": info["file"], "color": info.get("color")}
        for name, info in mapping.items()
        if info.get("file")
    }
    MAP_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        "скачано: %d, уже было: %d, не вышло: %d, в карте: %d"
        % (downloaded, skipped, failed, len(clean))
    )


if __name__ == "__main__":
    asyncio.run(main())
