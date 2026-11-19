"""Беттинг-центр: турниры и матчи с вероятностями на одной странице.

Проблема, которую решает: на сайте не было очевидной точки входа — человек
не понимал, где смотреть матчи. Здесь сверху список турниров, ниже матчи,
сгруппированные по соревнованиям, с вероятностями и ссылками.
"""

from __future__ import annotations

import asyncio
import html
import os
from pathlib import Path

from football_analyzer.analysis.forecast import MSK, build_forecast
from football_analyzer.analysis.service import CUP_PREFIXES, fit_all_models
from football_analyzer.storage import db
from football_analyzer.team_logos import team_html
from football_analyzer.teams_ru import to_russian

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "landing"
BASE_URL = "https://footballinho.ru"
BOT_URL = "https://t.me/footballizator_bot?start=ch_site"

PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<meta name="yandex-verification" content="2d7c2dde8b71028d">
<meta property="og:image" content="https://footballinho.ru/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<link rel="canonical" href="{canonical}">
<link rel="stylesheet" href="/fonts/onest.css">
<link rel="stylesheet" href="/style.css">
</head>
<body>
<header class="nav">
  <a class="brand" href="/">⚽ Football Analyzer</a>
  <nav>
    <a href="/line/">Беттинг-центр</a>
    <a href="/live/">Live</a>
    <a href="/schedule/">Расписание</a>
    <a href="/results/">Результаты</a>
    <a href="/table/">Таблицы</a>
    <a href="/match-center/">Матч-центр</a>
    <a href="/blog/">Статьи</a>
    <a class="cta" href="{bot}">Открыть бота</a>
  </nav>
</header>
<main>
{content}
</main>
<footer>
  <p>
    <a href="/offer.html">Оферта</a> ·
    <a href="/terms.html">Соглашение</a> ·
    <a href="/privacy.html">Политика ПД</a> ·
    <a href="/consent.html">Согласие</a> ·
    <a href="/rights.html">Права</a> ·
    <a href="/cookies.html">Cookie</a> ·
    <a href="#" data-cookie-settings>Настройки cookie</a>
  </p>
</footer>
</body>
</html>
"""


def slugify(value: str) -> str:
    import re

    value = value.lower().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", "-", value).strip("-") or "league"


def line_text(probability: float, label: str) -> str:
    if probability >= 1 - probability:
        return "ТБ %s · %.0f%%" % (label, probability * 100)
    return "ТМ %s · %.0f%%" % (label, (1 - probability) * 100)


async def build() -> None:
    pool = await db.connect(os.environ["DATABASE_URL"])
    rows = await db.upcoming_matches(pool, 14)
    model = await fit_all_models(pool)
    await pool.close()

    prepared = []
    for match in rows:
        competition = match["competition_id"]
        rates = (
            model if competition.startswith(CUP_PREFIXES) else model.league_rates.get(competition)
        )
        if rates is None:
            continue
        try:
            forecast = build_forecast(
                rates,
                competition_id=competition,
                home_team=match["home_team"],
                away_team=match["away_team"],
                kickoff_utc=match["kickoff_utc"],
            )
        except Exception:  # noqa: BLE001
            continue
        prepared.append((match, forecast))

    grouped: dict[str, list] = {}
    for match, forecast in prepared:
        grouped.setdefault(match["competition_id"], []).append((match, forecast))

    # Список турниров сверху
    chips = []
    sections = []
    for competition_id, items in sorted(
        grouped.items(), key=lambda pair: (-len(pair[1]), str(pair[1][0][0]["name_ru"]))
    ):
        items.sort(key=lambda pair: pair[0]["kickoff_utc"])
        name = str(items[0][0]["name_ru"])
        slug = slugify(competition_id)
        chips.append(
            '<a class="chip" href="#%s">%s · %d</a>' % (slug, html.escape(name), len(items))
        )
        match_rows = []
        for match, forecast in items[:12]:
            kickoff = match["kickoff_utc"].astimezone(MSK)
            match_rows.append(
                "<tr><td>%s</td>"
                '<td><a href="/match/%d/">%s — %s</a></td>'
                "<td>П1 %.0f%% · Х %.0f%% · П2 %.0f%%</td>"
                "<td>%s</td><td>%s</td><td>%s</td></tr>"
                % (
                    kickoff.strftime("%d.%m %H:%M"),
                    match["id"],
                    team_html(match["home_team"]),
                    team_html(match["away_team"]),
                    forecast.p_home * 100,
                    forecast.p_draw * 100,
                    forecast.p_away * 100,
                    line_text(forecast.total[2.5], "2,5"),
                    line_text(forecast.corners[9.5], "9,5"),
                    line_text(forecast.yellow[4.5], "4,5"),
                )
            )
        sections.append(
            '<section id="%s"><h2 class="section-title">%s</h2>'
            '<table class="matches"><thead><tr><th>Время</th><th>Матч</th>'
            "<th>Исход</th><th>Тотал</th><th>Углы</th><th>ЖК</th></tr></thead>"
            "<tbody>%s</tbody></table>"
            '<p class="muted"><a href="/match-center/%s/">Все матчи турнира →</a></p>'
            "</section>" % (slug, html.escape(name), "".join(match_rows), slug)
        )

    content = (
        "<h1>Беттинг-центр</h1>"
        '<p class="muted">%d матчей · %d турниров · вероятности пересчитываются каждый час</p>'
        '<div class="filters">%s</div>%s'
        % (len(prepared), len(grouped), "".join(chips), "".join(sections))
    )

    path = OUT / "line" / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        PAGE.format(
            title="Беттинг-центр: матчи и вероятности по турнирам | Football Analyzer",
            description="Все ближайшие футбольные матчи с вероятностями исходов, "
            "тоталов, углов и карточек. Список турниров и расписание. 18+",
            canonical="%s/line/" % BASE_URL,
            content=content,
            bot=BOT_URL,
        ),
        encoding="utf-8",
    )
    print("беттинг-центр: %d матчей, %d турниров" % (len(prepared), len(grouped)))


if __name__ == "__main__":
    asyncio.run(build())
