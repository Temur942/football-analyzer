"""Эмблемы команд: отрисовка с запасным вариантом.

Карта «название → файл» лежит в docs/landing/logos/teams.json и собирается
scripts/sync_logos.py. Если для команды картинки нет, показываем только
название — страница не должна ломаться из-за отсутствующего файла.
"""

from __future__ import annotations

import html
import json
from functools import lru_cache
from pathlib import Path

from football_analyzer.teams_ru import to_russian

MAP_PATH = Path(__file__).resolve().parents[2] / "docs" / "landing" / "logos" / "teams.json"


@lru_cache(maxsize=1)
def mapping() -> dict[str, dict]:
    try:
        return json.loads(MAP_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def team_html(name: str, *, with_badge: bool = True) -> str:
    """Название команды по-русски, рядом — эмблема, если она есть."""
    russian = html.escape(to_russian(name))
    info = mapping().get(name)
    if not info or not info.get("file"):
        return russian
    return (
        '<span class="team"><img class="team-logo" src="%s" alt="" loading="lazy" '
        'width="18" height="18"><span>%s</span></span>' % (info["file"], russian)
    )
