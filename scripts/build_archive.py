"""Архив прошлых сезонов: таблицы и результаты по каждому турниру.

Зачем: запросы «таблица АПЛ 2025», «результаты прошлого сезона» — большой
хвост, который у нас не был закрыт. Данные уже в базе, внешние источники
не нужны, лицензионных рисков нет.
"""

from __future__ import annotations

import asyncio
import html
import os
from pathlib import Path

from football_analyzer.competitions import load_competitions
from football_analyzer.storage import db
from football_analyzer.team_logos import team_html
from football_analyzer.team_slug import team_slug

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "landing" / "archive"
BASE = "https://footballinho.ru"
BOT = "https://t.me/footballizator_bot?start=ch_site"
CURRENT_SEASONS_BACK = 3

PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<meta name="yandex-verification" content="2d7c2dde8b71028d">
<meta property="og:image" content="https://footballinho.ru/og.png">
<link rel="canonical" href="{canonical}">
<link rel="stylesheet" href="/fonts/onest.css">
<link rel="stylesheet" href="/style.css">
</head>
<body>
<header class="nav">
  <a class="brand" href="/">FOOTBALLINHO</a>
  <nav>
    <a href="/line/">Беттинг-центр</a>
    <a href="/live/">Live</a>
    <a href="/schedule/">Расписание</a>
    <a href="/results/">Результаты</a>
    <a href="/table/">Таблицы</a>
    <a href="/archive/">Архив</a>
    <a class="cta" href="{bot}">Открыть бота</a>
  </nav>
</header>
<main>
{content}
</main>
<footer>
  <p>18+. Материалы — аналитика и вероятности, а не призыв к ставкам.</p>
  <p>Самозанятый Хидиров Темур Орифжонович · ИНН 021904477296 · khidrov@yandex.ru</p>
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
    return re.sub(r"[^a-zа-я0-9]+", "-", value).strip("-") or "x"


def build_table(matches: list) -> list[dict]:
    stats: dict[str, dict] = {}
    for row in matches:
        home, away = row["home_team"], row["away_team"]
        hg, ag = int(row["home_goals"]), int(row["away_goals"])
        for team in (home, away):
            stats.setdefault(team, {"played": 0, "won": 0, "drawn": 0, "lost": 0, "gf": 0, "ga": 0})
        stats[home]["played"] += 1
        stats[away]["played"] += 1
        stats[home]["gf"] += hg
        stats[home]["ga"] += ag
        stats[away]["gf"] += ag
        stats[away]["ga"] += hg
        if hg > ag:
            stats[home]["won"] += 1
            stats[away]["lost"] += 1
        elif hg == ag:
            stats[home]["drawn"] += 1
            stats[away]["drawn"] += 1
        else:
            stats[away]["won"] += 1
            stats[home]["lost"] += 1
    rows = [
        {
            **values,
            "team": team,
            "points": values["won"] * 3 + values["drawn"],
            "diff": values["gf"] - values["ga"],
        }
        for team, values in stats.items()
    ]
    rows.sort(key=lambda item: (-item["points"], -item["diff"], -item["gf"]))
    return rows


async def build() -> None:
    pool = await db.connect(os.environ["DATABASE_URL"])
    competitions = {getattr(c, "id", ""): getattr(c, "name_ru", "") for c in load_competitions()}

    async with pool.acquire() as conn:
        seasons = [
            row["season"]
            for row in await conn.fetch(
                "SELECT season FROM matches GROUP BY season ORDER BY season DESC LIMIT $1",
                CURRENT_SEASONS_BACK + 1,
            )
        ]
        matches = await conn.fetch(
            "SELECT season, competition_id, home_team, away_team, home_goals, away_goals"
            " FROM matches WHERE status = 'finished' AND home_goals IS NOT NULL"
        )
    await pool.close()

    past = seasons[1 : CURRENT_SEASONS_BACK + 1]
    if not past:
        print("прошлых сезонов в базе нет")
        return

    by_key: dict[tuple[str, str], list] = {}
    for row in matches:
        if row["season"] in past:
            by_key.setdefault((row["season"], row["competition_id"]), []).append(row)

    written = 0
    for season in past:
        season_label = season.replace("-", "/")
        cards = []
        for (season_key, competition_id), rows in sorted(by_key.items()):
            if season_key != season:
                continue
            if competition_id.startswith(("uefa-", "int-", "fifa-")):
                continue
            table = build_table(rows)
            if len(table) < 6:
                continue
            name = competitions.get(competition_id, competition_id)
            slug = slugify(competition_id)
            body = "".join(
                "<tr><td>%d</td><td>%s</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td>"
                "<td>%d:%d</td><td>%d</td></tr>"
                % (
                    index,
                    '<a href="/team/%s/">%s</a>'
                    % (team_slug(item["team"]), team_html(item["team"])),
                    item["played"],
                    item["won"],
                    item["drawn"],
                    item["lost"],
                    item["gf"],
                    item["ga"],
                    item["points"],
                )
                for index, item in enumerate(table, start=1)
            )
            content = (
                "<h1>%s: таблица сезона %s</h1>"
                % (html.escape(name), season_label)
                + '<p class="muted">Итоговая таблица по завершённым матчам нашей базы. '
                "Текущий сезон — в разделе «Таблицы».</p>"
                "<table><thead><tr><th>#</th><th>Команда</th><th>И</th><th>В</th><th>Н</th>"
                "<th>П</th><th>Голы</th><th>О</th></tr></thead><tbody>%s</tbody></table>"
                % body
                + '<p class="muted"><a href="/table/%s/">Текущая таблица</a> · '
                '<a href="/archive/%s/">Все турниры сезона</a></p>' % (slug, season)
            )
            path = OUT / season / slug / "index.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                PAGE.format(
                    title="%s: таблица сезона %s — FOOTBALLINHO" % (name, season_label),
                    description="Итоговая таблица %s за сезон %s: очки, разница мячей, "
                    "места команд. 18+" % (name, season_label),
                    canonical="%s/archive/%s/%s/" % (BASE, season, slug),
                    content=content,
                    bot=BOT,
                ),
                encoding="utf-8",
            )
            cards.append(
                '<a class="card" href="/archive/%s/%s/"><h3>%s</h3>'
                '<p class="muted">команд: %d</p></a>'
                % (season, slug, html.escape(name), len(table))
            )
            written += 1

        index_content = (
            "<h1>Сезон %s: итоговые таблицы</h1>"
            % season_label
            + '<p class="muted">Все турниры сезона. Текущий сезон — '
            '<a href="/table/">в таблицах</a>.</p>' + '<div class="grid">%s</div>' % "".join(cards)
        )
        index_path = OUT / season / "index.html"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            PAGE.format(
                title="Футбол %s: итоговые таблицы всех турниров — FOOTBALLINHO" % season_label,
                description="Итоговые таблицы сезона %s: места, очки, разница мячей. 18+"
                % season_label,
                canonical="%s/archive/%s/" % (BASE, season),
                content=index_content,
                bot=BOT,
            ),
            encoding="utf-8",
        )

    seasons_cards = "".join(
        '<a class="card" href="/archive/%s/"><h3>Сезон %s</h3>'
        '<p class="muted">итоговые таблицы</p></a>' % (season, season.replace("-", "/"))
        for season in past
    )
    (OUT / "index.html").parent.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(
        PAGE.format(
            title="Архив футбольных сезонов: итоговые таблицы — FOOTBALLINHO",
            description="Архив прошлых сезонов: итоговые таблицы турниров, очки и разница "
            "мячей. 18+",
            canonical="%s/archive/" % BASE,
            content="<h1>Архив сезонов</h1>"
            '<p class="muted">Прошлые сезоны по всем турнирам.</p>'
            '<div class="grid">%s</div>' % seasons_cards,
            bot=BOT,
        ),
        encoding="utf-8",
    )
    print("архив: страниц %d, сезонов %d" % (written, len(past)))


if __name__ == "__main__":
    asyncio.run(build())
