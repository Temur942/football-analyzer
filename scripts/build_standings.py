"""Турнирные таблицы по соревнованиям.

Зачем: по Вордстату кластер «таблица» — 43,8 млн показов, в 100 раз больше, чем
«прогнозы». Люди ищут таблицу, а не вероятности, поэтому страница таблицы —
точка входа: рядом ставим ближайший матч команды с вероятностями и ссылкой в бота.

Считаем из своей базы завершённых матчей: очки 3-1-0, разница мячей, форма.
"""

from __future__ import annotations

import asyncio
import html
import os
import re
from pathlib import Path

from football_analyzer.analysis.forecast import MSK, build_forecast
from football_analyzer.analysis.service import CUP_PREFIXES, fit_all_models
from football_analyzer.competitions import load_competitions
from football_analyzer.storage import db
from football_analyzer.teams_ru import to_russian

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "landing" / "table"
BASE_URL = "https://footballinho.ru"
BOT_URL = "https://t.me/footballizator_bot?start=ch_site"


def slugify(value: str) -> str:
    value = value.lower().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", "-", value).strip("-") or "league"


def season_label(season: str) -> str:
    return season.replace("-", "/")


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
  <a class="brand" href="/">⚽ Football Analyzer</a>
  <nav>
    <a href="/line/">Беттинг-центр</a>
    <a href="/live/">Live</a>
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


def build_table(matches: list) -> list[dict]:
    stats: dict[str, dict] = {}
    form: dict[str, list[str]] = {}
    for row in matches:
        home = row["home_team"]
        away = row["away_team"]
        home_goals = int(row["home_goals"])
        away_goals = int(row["away_goals"])
        for team in (home, away):
            stats.setdefault(
                team,
                {"played": 0, "won": 0, "drawn": 0, "lost": 0, "gf": 0, "ga": 0},
            )
        stats[home]["played"] += 1
        stats[away]["played"] += 1
        stats[home]["gf"] += home_goals
        stats[home]["ga"] += away_goals
        stats[away]["gf"] += away_goals
        stats[away]["ga"] += home_goals
        if home_goals > away_goals:
            stats[home]["won"] += 1
            stats[away]["lost"] += 1
            form.setdefault(home, []).append("В")
            form.setdefault(away, []).append("П")
        elif home_goals == away_goals:
            stats[home]["drawn"] += 1
            stats[away]["drawn"] += 1
            form.setdefault(home, []).append("Н")
            form.setdefault(away, []).append("Н")
        else:
            stats[away]["won"] += 1
            stats[home]["lost"] += 1
            form.setdefault(away, []).append("В")
            form.setdefault(home, []).append("П")

    rows = []
    for team, values in stats.items():
        points = values["won"] * 3 + values["drawn"]
        rows.append(
            {
                **values,
                "team": team,
                "points": points,
                "diff": values["gf"] - values["ga"],
                "form": form.get(team, [])[-5:],
            }
        )
    rows.sort(key=lambda item: (-item["points"], -item["diff"], -item["gf"]))
    return rows


async def build() -> None:
    pool = await db.connect(os.environ["DATABASE_URL"])
    competitions = load_competitions()
    model = await fit_all_models(pool)

    async with pool.acquire() as conn:
        latest_season = await conn.fetchval("SELECT max(season) FROM matches")
        matches = await conn.fetch(
            "SELECT competition_id, home_team, away_team, home_goals, away_goals, kickoff_utc"
            " FROM matches WHERE season = $1 AND status = 'finished'"
            " AND home_goals IS NOT NULL ORDER BY kickoff_utc",
            latest_season,
        )
    upcoming = await db.upcoming_matches(pool, 14)
    await pool.close()

    by_competition: dict[str, list] = {}
    for row in matches:
        by_competition.setdefault(row["competition_id"], []).append(row)

    upcoming_by_competition: dict[str, list] = {}
    for row in upcoming:
        upcoming_by_competition.setdefault(row["competition_id"], []).append(row)

    cards = []
    for competition in competitions:
        competition_id = getattr(competition, "id", "")
        name_ru = getattr(competition, "name_ru", competition_id)
        if not competition_id or competition_id.startswith(CUP_PREFIXES):
            continue
        rows = build_table(by_competition.get(competition_id, []))
        if len(rows) < 4:
            continue
        slug = slugify(competition_id)

        table_rows = []
        for index, item in enumerate(rows, start=1):
            form = " ".join(item["form"])
            table_rows.append(
                "<tr><td>%d</td><td>%s</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td>"
                "<td>%d:%d</td><td>%+d</td><td><b>%d</b></td><td class='muted'>%s</td></tr>"
                % (
                    index,
                    html.escape(to_russian(item["team"])),
                    item["played"],
                    item["won"],
                    item["drawn"],
                    item["lost"],
                    item["gf"],
                    item["ga"],
                    item["diff"],
                    item["points"],
                    form,
                )
            )

        # Блок ближайших матчей с вероятностями — мостик из поиска в бота.
        next_rows = []
        for match in upcoming_by_competition.get(competition_id, [])[:3]:
            rates = (
                model
                if competition_id.startswith(CUP_PREFIXES)
                else model.league_rates.get(competition_id)
            )
            if rates is None:
                continue
            try:
                forecast = build_forecast(
                    rates,
                    competition_id=competition_id,
                    home_team=match["home_team"],
                    away_team=match["away_team"],
                    kickoff_utc=match["kickoff_utc"],
                )
            except Exception:  # noqa: BLE001
                continue
            kickoff = match["kickoff_utc"].astimezone(MSK)
            next_rows.append(
                '<tr><td>%s</td><td><a href="/match/%d/">%s — %s</a></td>'
                "<td>П1 %.0f%% · Х %.0f%% · П2 %.0f%%</td></tr>"
                % (
                    kickoff.strftime("%d.%m %H:%M"),
                    match["id"],
                    html.escape(to_russian(match["home_team"])),
                    html.escape(to_russian(match["away_team"])),
                    forecast.p_home * 100,
                    forecast.p_draw * 100,
                    forecast.p_away * 100,
                )
            )
        next_block = (
            '<h2 class="section-title">Ближайшие матчи и вероятности</h2>'
            "<table><thead><tr><th>Время</th><th>Матч</th><th>Исход</th></tr></thead>"
            "<tbody>%s</tbody></table>" % "".join(next_rows)
            if next_rows
            else ""
        )

        content = (
            "<h1>Турнирная таблица %s %s</h1>"
            '<p class="muted">Очки, разница мячей и форма по последним 5 матчам. '
            "Пересчитывается автоматически.</p>"
            "<table><thead><tr><th>#</th><th>Команда</th><th>И</th><th>В</th><th>Н</th>"
            "<th>П</th><th>Голы</th><th>±</th><th>О</th><th>Форма</th></tr></thead>"
            "<tbody>%s</tbody></table>%s"
            % (html.escape(name_ru), season_label(latest_season), "".join(table_rows), next_block)
        )

        path = OUT / slug / "index.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            PAGE.format(
                title="Турнирная таблица %s %s — Football Analyzer"
                % (html.escape(name_ru), season_label(latest_season)),
                description="%s: турнирная таблица, очки, разница мячей, форма команд "
                "и вероятности на ближайшие матчи. 18+" % name_ru,
                canonical="%s/table/%s/" % (BASE_URL, slug),
                content=content,
                bot=BOT_URL,
            ),
            encoding="utf-8",
        )
        cards.append(
            '<a class="card" href="/table/%s/">'
            '<h3>%s</h3><p class="muted">%s · команд: %d</p></a>'
            % (slug, html.escape(name_ru), season_label(latest_season), len(rows))
        )

    index_content = (
        "<h1>Турнирные таблицы</h1>"
        '<p class="muted">Сезон %s. Выберите турнир.</p>'
        '<div class="grid">%s</div>' % (season_label(latest_season), "".join(cards))
    )
    index_path = OUT / "index.html"
    index_path.write_text(
        PAGE.format(
            title="Турнирные таблицы футбольных чемпионатов — Football Analyzer",
            description="Турнирные таблицы 20 соревнований: очки, разница мячей, форма. "
            "Рядом — вероятности на ближайшие матчи. 18+",
            canonical="%s/table/" % BASE_URL,
            content=index_content,
            bot=BOT_URL,
        ),
        encoding="utf-8",
    )
    print("таблиц собрано: %d (сезон %s)" % (len(cards), latest_season))


if __name__ == "__main__":
    asyncio.run(build())
