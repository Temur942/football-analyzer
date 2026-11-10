"""Матч-центр: страницы матчей, соревнований и общий список с фильтрами.

Бесплатная часть — короткая выжимка (тотал, углы, карточки, исход, счёт).
Полная раскладка остаётся в боте: иначе подписка теряет смысл. Это главное
правило при генерации страниц — показывать ровно столько, чтобы человек
понял ценность и открыл бота.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
from pathlib import Path

from football_analyzer.analysis.forecast import MSK, build_forecast
from football_analyzer.analysis.service import CUP_PREFIXES, fit_all_models
from football_analyzer.storage import db
from football_analyzer.teams_ru import to_russian

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "landing"
BASE_URL = "https://footballinho.ru"
BOT_URL = "https://t.me/footballizator_bot"

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
<meta name="yandex-verification" content="2d7c2dde8b71028d">
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
    <a class="cta" href="{bot}">Открыть бота</a>
  </nav>
</header>
<main>
{content}
<div class="card" style="margin-top:32px">
  <h3>Полная раскладка — в боте</h3>
  <p>На сайте — только главное. В боте: индивидуальные тоталы каждой команды,
     все линии углов и карточек, вероятный счёт и доставка за 5 часов до матча.</p>
  <p><a class="button" href="{bot}">Открыть бота</a></p>
</div>
</main>
<footer>
  <p>18+. Материалы — аналитика и вероятности, а не призыв к ставкам.</p>
  <p>Самозанятый Хидиров Темур Орифжонович · ИНН 021904477296 · khidrov@yandex.ru</p>
</footer>
</body>
</html>
"""

FILTER_JS = """
<script>
const search = document.getElementById('q');
const dateFilter = document.getElementById('d');
function apply() {
  const q = search.value.toLowerCase();
  const d = dateFilter.value;
  document.querySelectorAll('#rows tr').forEach(row => {
    const text = row.dataset.text;
    const date = row.dataset.date;
    row.style.display = (text.includes(q) && (!d || date === d)) ? '' : 'none';
  });
}
search.addEventListener('input', apply);
dateFilter.addEventListener('change', apply);
</script>
"""


def slugify(value: str) -> str:
    value = value.lower().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", "-", value).strip("-") or "league"


def best(probability: float, label: str) -> str:
    if probability >= 1 - probability:
        return "ТБ %s — %.0f%%" % (label, probability * 100)
    return "ТМ %s — %.0f%%" % (label, (1 - probability) * 100)


def render_match(match, forecast, competition_name: str) -> str:
    kickoff = match["kickoff_utc"].astimezone(MSK)
    home = html.escape(to_russian(match["home_team"]))
    away = html.escape(to_russian(match["away_team"]))
    score = " · ".join("%d:%d — %.0f%%" % (h, a, p * 100) for h, a, p in forecast.scores[:3])
    rows = [
        (
            "Исход",
            "П1 %.0f%% · Х %.0f%% · П2 %.0f%%"
            % (forecast.p_home * 100, forecast.p_draw * 100, forecast.p_away * 100),
        ),
        (
            "Тотал матча",
            best(forecast.total[2.5], "2,5") + " · " + best(forecast.total[1.5], "1,5"),
        ),
        ("Первый тайм", best(forecast.first_half[0.5], "0,5")),
        ("Угловые", best(forecast.corners[9.5], "9,5")),
        ("Жёлтые карточки", best(forecast.yellow[4.5], "4,5")),
        ("Вероятные счета", score),
    ]
    table = "".join("<tr><th>%s</th><td>%s</td></tr>" % (name, value) for name, value in rows)
    title = "%s — %s: прогноз и вероятности %s" % (home, away, kickoff.strftime("%d.%m.%Y"))
    description = (
        "%s — %s, %s МСК: вероятности тотала, углов и карточек по модели. "
        "Полная раскладка в боте за 5 часов до матча. 18+"
        % (home, away, kickoff.strftime("%d.%m %H:%M"))
    )[:300]
    content = '<p class="muted">%s · %s МСК</p><h1>%s — %s</h1><table>%s</table>' % (
        html.escape(competition_name),
        kickoff.strftime("%d.%m %H:%M"),
        home,
        away,
        table,
    )
    jsonld = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "SportsEvent",
            "name": "%s — %s" % (home, away),
            "startDate": match["kickoff_utc"].isoformat(),
            "eventStatus": "https://schema.org/EventScheduled",
        },
        ensure_ascii=False,
    )
    return PAGE.format(
        title=html.escape(title),
        description=html.escape(description),
        canonical="%s/match/%d/" % (BASE_URL, match["id"]),
        jsonld=jsonld,
        content=content,
        bot=BOT_URL,
    )


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

    match_dir = OUT / "match"
    match_dir.mkdir(parents=True, exist_ok=True)
    for match, forecast in prepared:
        directory = match_dir / str(match["id"])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "index.html").write_text(
            render_match(match, forecast, str(match["name_ru"])), encoding="utf-8"
        )

    all_rows = []
    for match, forecast in prepared:
        kickoff = match["kickoff_utc"].astimezone(MSK)
        home = html.escape(to_russian(match["home_team"]))
        away = html.escape(to_russian(match["away_team"]))
        link = "/match/%d/" % match["id"]
        text = ("%s %s %s" % (home, away, match["name_ru"])).lower()
        all_rows.append(
            '<tr data-date="%s" data-text="%s"><td>%s</td><td>%s</td>'
            '<td><a href="%s">%s — %s</a></td><td>%s</td><td>%s</td><td>%s</td></tr>'
            % (
                kickoff.strftime("%Y-%m-%d"),
                html.escape(text, quote=True),
                kickoff.strftime("%d.%m %H:%M"),
                html.escape(str(match["name_ru"])),
                link,
                home,
                away,
                best(forecast.total[2.5], "2,5"),
                best(forecast.corners[9.5], "9,5"),
                best(forecast.yellow[4.5], "4,5"),
            )
        )

    dates = sorted(
        {match["kickoff_utc"].astimezone(MSK).strftime("%Y-%m-%d") for match, _ in prepared}
    )
    options = "".join('<option value="%s">%s</option>' % (day, day) for day in dates)
    index_content = """<h1>Матч-центр</h1>
<p class="muted">Ближайшие матчи и то, что модель считает наиболее вероятным.</p>
<div class="filters">
  <input id="q" type="search" placeholder="Команда или соревнование">
  <select id="d"><option value="">Все даты</option>%s</select>
</div>
<table><thead><tr><th>Время</th><th>Соревнование</th><th>Матч</th>
<th>Тотал</th><th>Углы</th><th>ЖК</th></tr></thead>
<tbody id="rows">%s</tbody></table>%s""" % (options, "".join(all_rows), FILTER_JS)

    (OUT / "match-center" / "index.html").write_text(
        PAGE.format(
            title="Матч-центр: прогнозы и вероятности на ближайшие матчи",
            description="Расписание и вероятности по 20 соревнованиям: тотал, "
            "углы, карточки, исход. Прогнозы в боте за 5 часов до матча. 18+",
            canonical="%s/match-center/" % BASE_URL,
            jsonld='{"@context":"https://schema.org","@type":"ItemList"}',
            content=index_content,
            bot=BOT_URL,
        ),
        encoding="utf-8",
    )

    by_competition: dict[str, list] = {}
    for match, forecast in prepared:
        by_competition.setdefault(match["competition_id"], []).append((match, forecast))
    slugs = []
    for competition_id, items in sorted(by_competition.items()):
        items.sort(key=lambda pair: pair[0]["kickoff_utc"])
        name = str(items[0][0]["name_ru"])
        slug = slugify(competition_id)
        slugs.append(slug)
        body = []
        for match, forecast in items:
            kickoff = match["kickoff_utc"].astimezone(MSK)
            home = html.escape(to_russian(match["home_team"]))
            away = html.escape(to_russian(match["away_team"]))
            body.append(
                '<tr><td>%s</td><td><a href="/match/%d/">%s — %s</a></td>'
                "<td>%s</td><td>%s</td><td>%s</td></tr>"
                % (
                    kickoff.strftime("%d.%m %H:%M"),
                    match["id"],
                    home,
                    away,
                    best(forecast.total[2.5], "2,5"),
                    best(forecast.corners[9.5], "9,5"),
                    best(forecast.yellow[4.5], "4,5"),
                )
            )
        content = (
            "<h1>%s</h1><p class='muted'>Ближайшие матчи и вероятности по модели.</p>"
            "<table><thead><tr><th>Время</th><th>Матч</th><th>Тотал</th>"
            "<th>Углы</th><th>ЖК</th></tr></thead><tbody>%s</tbody></table>"
            % (html.escape("Матч-центр: %s" % name), "".join(body))
        )
        directory = OUT / "match-center" / slug
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "index.html").write_text(
            PAGE.format(
                title=html.escape("Прогнозы на %s — вероятности матчей | Football Analyzer" % name),
                description=html.escape(
                    "%s: расписание и вероятности — тотал, углы, карточки. 18+" % name
                ),
                canonical="%s/match-center/%s/" % (BASE_URL, slug),
                jsonld='{"@context":"https://schema.org","@type":"ItemList"}',
                content=content,
                bot=BOT_URL,
            ),
            encoding="utf-8",
        )

    urls = [
        "/",
        "/prices.html",
        "/offer.html",
        "/terms.html",
        "/privacy.html",
        "/blog/",
        "/match-center/",
    ]
    blog_dir = OUT / "blog"
    if blog_dir.is_dir():
        urls += [
            "/blog/%s" % path.name
            for path in sorted(blog_dir.glob("*.html"))
            if path.name != "index.html"
        ]
    urls += ["/match-center/%s/" % slug for slug in slugs]
    urls += ["/match/%d/" % match["id"] for match, _ in prepared]
    sitemap = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    sitemap += ["  <url><loc>%s%s</loc></url>" % (BASE_URL, url) for url in urls]
    sitemap.append("</urlset>")
    (OUT / "sitemap.xml").write_text("\n".join(sitemap) + "\n", encoding="utf-8")
    print(
        "матчей: %d | соревнований: %d | url в sitemap: %d" % (len(prepared), len(slugs), len(urls))
    )


if __name__ == "__main__":
    asyncio.run(build())
