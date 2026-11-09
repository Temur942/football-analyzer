"""Матч-центр: страницы по соревнованиям с вероятностями и ссылкой в бота.

Генерирует статические страницы (nginx отдаёт их напрямую), обновлять можно
по таймеру. Каждая страница — посадочная: заголовок под запрос, таблица
матчей, кнопка в бота и перелинковка между соревнованиями.
"""

from __future__ import annotations

import asyncio
import html
import os
import re
from datetime import datetime
from pathlib import Path

from football_analyzer.analysis.forecast import MSK, build_forecast
from football_analyzer.analysis.service import CUP_PREFIXES, fit_all_models
from football_analyzer.storage import db
from football_analyzer.teams_ru import to_russian

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "landing" / "match-center"
BASE_URL = "https://footballinho.ru"

PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{canonical}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<link rel="stylesheet" href="/fonts/onest.css">
<link rel="stylesheet" href="/style.css">
<script type="application/ld+json">{jsonld}</script>
</head>
<body>
<header class="nav">
  <a class="brand" href="/">⚽ Football Analyzer</a>
  <nav>
    <a href="/match-center/">Матч-центр</a>
    <a href="/blog/">Статьи</a>
    <a href="/#prices">Тарифы</a>
    <a class="cta" href="https://t.me/footballizator_bot">Открыть бота</a>
  </nav>
</header>
<main>
<h1>{h1}</h1>
<p class="muted">{lead}</p>
{content}
<div class="card" style="margin-top:32px">
  <h3>Что в боте</h3>
  <p>Прогноз приходит за 5 часов до матча: тотал, исход, счёт, голы команд,
     углы и карточки — в процентах. Первые 3 дня бесплатно.</p>
  <p><a class="button" href="https://t.me/footballizator_bot">Открыть бота</a></p>
</div>
</main>
<footer>
  <p>18+. Материалы — аналитика и вероятности, а не призыв к ставкам.</p>
  <p>Самозанятый Хидиров Темур Орифжонович · ИНН 021904477296 · khidrov@yandex.ru</p>
</footer>
</body>
</html>
"""


def slugify(value: str) -> str:
    value = value.lower().replace("ё", "е")
    value = re.sub(r"[^a-zа-я0-9]+", "-", value).strip("-")
    return value or "league"


def best_line(probability: float, line_label: str) -> str:
    over = probability
    under = 1 - probability
    if over >= under:
        return "ТБ %s — %.0f%%" % (line_label, over * 100)
    return "ТМ %s — %.0f%%" % (line_label, under * 100)


def row_html(match, forecast) -> str:
    kickoff = match["kickoff_utc"].astimezone(MSK).strftime("%d.%m %H:%M")
    home = html.escape(to_russian(match["home_team"]))
    away = html.escape(to_russian(match["away_team"]))
    link = "https://t.me/footballizator_bot?start=match_%d" % match["id"]
    part1 = "<td>%s</td>" % kickoff
    part2 = '<td><a href="%s">%s — %s</a></td>' % (link, home, away)
    total = best_line(forecast.total[2.5], "2,5")
    corners = best_line(forecast.corners[9.5], "9,5")
    cards = best_line(forecast.yellow[4.5], "4,5")
    return "<tr>%s%s<td>%s</td><td>%s</td><td>%s</td></tr>" % (part1, part2, total, corners, cards)


async def build() -> None:
    pool = await db.connect(os.environ["DATABASE_URL"])
    rows = await db.upcoming_matches(pool, 14)
    model = await fit_all_models(pool)
    await pool.close()

    by_competition: dict[str, list] = {}
    for row in rows:
        by_competition.setdefault(row["competition_id"], []).append(row)

    OUT.mkdir(parents=True, exist_ok=True)
    links: list[str] = []
    pages: list[tuple[str, str, str]] = []  # slug, name, url

    for competition_id, matches in sorted(by_competition.items()):
        matches.sort(key=lambda row: row["kickoff_utc"])
        name = str(matches[0]["name_ru"])
        slug = slugify(competition_id)
        table = []
        for match in matches:
            rates = (
                model
                if competition_id.startswith(CUP_PREFIXES)
                else model.league_rates.get(competition_id)
            )
            if rates is None:
                continue
            forecast = build_forecast(
                rates,
                competition_id=competition_id,
                home_team=match["home_team"],
                away_team=match["away_team"],
                kickoff_utc=match["kickoff_utc"],
            )
            table.append(row_html(match, forecast))

        content = (
            "<table><thead><tr><th>Время, МСК</th><th>Матч</th>"
            "<th>Тотал</th><th>Углы</th><th>ЖК</th></tr></thead><tbody>%s</tbody></table>"
            % "".join(table)
        )
        title = "Прогнозы на %s — вероятности матчей | Football Analyzer" % name
        description = (
            "%s: расписание и вероятности по модели — тотал, углы, карточки. "
            "Прогнозы в боте за 5 часов до матча. 18+" % name
        )[:300]
        jsonld = (
            '{"@context":"https://schema.org","@type":"SportsEvent",'
            '"name":"%s","startDate":"%s","eventStatus":"https://schema.org/EventScheduled"}'
            % (html.escape(name, quote=True), matches[0]["kickoff_utc"].isoformat())
        )
        page = PAGE.format(
            title=html.escape(title),
            description=html.escape(description),
            canonical="%s/match-center/%s/" % (BASE_URL, slug),
            h1=html.escape("Матч-центр: %s" % name),
            lead="Ближайшие матчи и то, что модель считает наиболее вероятным. "
            "Полная раскладка — в боте.",
            content=content,
            jsonld=jsonld,
        )
        directory = OUT / slug
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "index.html").write_text(page, encoding="utf-8")
        links.append(
            '<li><a href="/match-center/%s/">%s</a> — %d матчей</li>' % (slug, name, len(matches))
        )
        pages.append((slug, name, "%s/match-center/%s/" % (BASE_URL, slug)))

    index = PAGE.format(
        title="Матч-центр: расписание и вероятности | Football Analyzer",
        description="Матчи по 20 соревнованиям с вероятностями: тотал, углы, "
        "карточки. Прогнозы в боте за 5 часов до игры. 18+",
        canonical="%s/match-center/" % BASE_URL,
        h1="Матч-центр",
        lead="Выберите соревнование: расписание и вероятности по модели.",
        content="<ul>%s</ul>" % "".join(links),
        jsonld='{"@context":"https://schema.org","@type":"ItemList"}',
    )
    (OUT / "index.html").write_text(index, encoding="utf-8")

    # sitemap: главная, тарифы, документы, блог, матч-центр
    urls = [
        "/",
        "/prices.html",
        "/offer.html",
        "/terms.html",
        "/privacy.html",
        "/blog/",
        "/match-center/",
    ]
    blog_dir = ROOT / "docs" / "landing" / "blog"
    if blog_dir.is_dir():
        urls += [
            "/blog/%s" % path.name
            for path in sorted(blog_dir.glob("*.html"))
            if path.name != "index.html"
        ]
    urls += ["/match-center/%s/" % slug for slug, _, _ in pages]
    sitemap = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for url in urls:
        sitemap.append("  <url><loc>%s%s</loc></url>" % (BASE_URL, url))
    sitemap.append("</urlset>")
    (ROOT / "docs" / "landing" / "sitemap.xml").write_text(
        "\n".join(sitemap) + "\n", encoding="utf-8"
    )
    print("соревнований:", len(pages), "| страниц в sitemap:", len(urls))


if __name__ == "__main__":
    asyncio.run(build())
