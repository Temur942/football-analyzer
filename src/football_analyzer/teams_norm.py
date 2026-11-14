"""Нормализация названий команд для сопоставления источников.

ESPN пишет «Manchester City», football-data — «Man City»; «Brighton & Hove
Albion» против «Brighton». Приводим к грубому ключу: без суффиксов и с
известными сокращениями. Точность важнее красоты — ключ должен совпадать.
"""

from __future__ import annotations

import re
import unicodedata

SUFFIXES = (
    " & hove albion",
    " albion",
    " city",
    " united",
    " town",
    " fc",
    " afc",
    " sc",
    " cf",
    " ac",
    " club",
)

ALIASES = {
    "man city": "manchester city",
    "man united": "manchester united",
    "man utd": "manchester united",
    "nott'm forest": "nottingham forest",
    "nottm forest": "nottingham forest",
    "sheffield utd": "sheffield united",
    "west brom": "west bromwich albion",
    "wolves": "wolverhampton",
    "spurs": "tottenham",
    "qpr": "queens park rangers",
    "st etienne": "saint etienne",
    "gladbach": "monchengladbach",
    "inter": "internazionale",
    "betis": "real betis",
    "sociedad": "real sociedad",
}


def normalize(name: str) -> str:
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("'", "").replace(".", "").strip()
    text = re.sub(r"\s+", " ", text)
    text = ALIASES.get(text, text)
    for suffix in SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    text = ALIASES.get(text, text)
    return text
