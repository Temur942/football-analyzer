"""Слаг команды для URL: «Zenit St Petersburg» → «zenit-st-petersburg».

Один и тот же слаг используют страницы команд и таблицы (ссылки),
поэтому функция живёт отдельно и не дублируется.
"""

from __future__ import annotations

import re

TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def team_slug(name: str) -> str:
    text = name.lower().replace("ё", "е")
    text = "".join(TRANSLIT.get(char, char) for char in text)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "team"
