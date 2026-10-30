"""Список соревнований: загрузка и проверка config/competitions.json.

Файл — единственный источник правды о том, что ведёт бот. Ключи источников
(espn, fbref) проверяются scripts/check_sources.py на живых данных до деплоя.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from football_analyzer.config import PROJECT_ROOT
from football_analyzer.models import Competition

DEFAULT_PATH = PROJECT_ROOT / "config" / "competitions.json"


def load_competitions(path: Path | None = None) -> list[Competition]:
    path = path or DEFAULT_PATH
    if not path.is_file():
        raise FileNotFoundError("не найден список соревнований: %s" % path)
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("competitions")
    if not isinstance(raw, list) or not raw:
        raise ValueError("в %s пустой список competitions" % path)
    return [Competition.model_validate(item) for item in raw]


@lru_cache(maxsize=1)
def active_competitions() -> list[Competition]:
    return [c for c in load_competitions() if c.is_active]


def competitions_with_espn() -> list[Competition]:
    """Соревнования, у которых есть ключ ESPN (источник расписания)."""
    return [c for c in active_competitions() if c.espn]
