"""Хлебные крошки в разметке (BreadcrumbList).

Зачем: Google и Яндекс показывают в сниппете путь «Главная › Таблицы › АПЛ»,
это повышает кликабельность. Разметку добавляем постпроцессом — не надо
править девять генераторов страниц.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANDING = ROOT / "docs" / "landing"
BASE = "https://footballinho.ru"

SECTIONS = {
    "match-center": "Матч-центр",
    "table": "Таблицы",
    "schedule": "Расписание",
    "results": "Результаты",
    "team": "Команды",
    "blog": "Статьи",
    "guide": "Разборы",
    "line": "Беттинг-центр",
    "live": "Live",
    "season": "Сезон",
    "russia": "Футбол России",
    "partners": "Партнёрам",
    "invite": "Приглашения",
    "match": "Матч",
    "predictions": "Прогнозы",
}

MARK = "BreadcrumbList"


def page_name(text: str) -> str:
    match = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S)
    if match:
        name = re.sub(r"<[^>]+>", "", match.group(1)).strip()
        if name:
            return html.unescape(name)[:80]
    match = re.search(r"<title>([^<]*)</title>", text)
    return html.unescape(match.group(1).split("—")[0].strip()) if match else ""


def crumbs_for(path: Path) -> list[tuple[str, str]]:
    relative = path.relative_to(LANDING)
    parts = [part for part in relative.parts if part != "index.html"]
    parts = [part[:-5] if part.endswith(".html") else part for part in parts]
    if not parts:
        return []
    url_parts: list[str] = []
    crumbs: list[tuple[str, str]] = [("Главная", BASE + "/")]
    for part in parts:
        url_parts.append(part)
        url = BASE + "/" + "/".join(url_parts) + ("/" if len(url_parts) < len(parts) else "")
        crumbs.append((SECTIONS.get(part, ""), url))
    return crumbs


def main() -> None:
    done = 0
    for path in LANDING.rglob("*.html"):
        if path.name.startswith("google") or path.name in ("404.html",):
            continue
        text = path.read_text(encoding="utf-8")
        if MARK in text:
            continue
        crumbs = crumbs_for(path)
        if len(crumbs) < 2:
            continue

        leaf = crumbs[-1]
        name = page_name(text) or leaf[0] or leaf[1]
        items = crumbs[:-1] + [(name, leaf[1])]

        data = {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": index, "name": label or "Раздел", "item": url}
                for index, (label, url) in enumerate(items, start=1)
            ],
        }
        script = '<script type="application/ld+json">%s</script>\n' % json.dumps(
            data, ensure_ascii=False
        )
        text = text.replace("</head>", script + "</head>", 1)
        path.write_text(text, encoding="utf-8")
        done += 1
    print("крошки добавлены на страниц:", done)


if __name__ == "__main__":
    main()
