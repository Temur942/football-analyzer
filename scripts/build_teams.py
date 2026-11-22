"""Страницы команд: матчи, результаты, форма и место в таблице.

Зачем: люди ищут «Зенит расписание», «Спартак результаты», «Динамо форма».
Таких запросов много, а страниц не было. Одна страница на команду связывает
расписание, результаты и таблицу — и раздаёт внутренние ссылки.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
from pathlib import Path

from football_analyzer.analysis.forecast import MSK, build_forecast
from football_analyzer.analysis.service import CUP_PREFIXES, fit_all_models
from football_analyzer.competitions import load_competitions
from football_analyzer.storage import db
from football_analyzer.team_logos import team_html
from football_analyzer.team_slug import team_slug
from football_analyzer.teams_ru import to_russian

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "landing" / "team"
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
<link rel="canonical" href="{canonical}">
<link rel="stylesheet" href="/fonts/onest.css">
<link rel="stylesheet" href="/style.css">
<script type="application/ld+json">{jsonld}</script>
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
    model = await fit_all_models(pool)
    upcoming = await db.upcoming_matches(pool, 14)

    async with pool.acquire() as conn:
        latest = await conn.fetchval("SELECT max(season) FROM matches")
        finished = await conn.fetch(
            "SELECT competition_id, home_team, away_team, home_goals, away_goals, kickoff_utc"
            " FROM matches WHERE season = $1 AND status = 'finished'"
            " AND home_goals IS NOT NULL ORDER BY kickoff_utc",
            latest,
        )
    await pool.close()

    season_label = latest.replace("-", "/")
    by_competition: dict[str, list] = {}
    for row in finished:
        by_competition.setdefault(row["competition_id"], []).append(dict(row))

    upcoming_by_team: dict[str, list] = {}
    for match in upcoming:
        upcoming_by_team.setdefault(match["home_team"], []).append(match)
        upcoming_by_team.setdefault(match["away_team"], []).append(match)

    written = 0
    for competition_id, rows in by_competition.items():
        if competition_id.startswith(CUP_PREFIXES):
            continue
        table = build_table(rows)
        position = {item["team"]: index for index, item in enumerate(table, start=1)}
        name = competitions.get(competition_id, competition_id)
        comp_slug = competition_id

        teams = {row["home_team"] for row in rows} | {row["away_team"] for row in rows}
        for team in teams:
            slug = team_slug(team)
            russian = to_russian(team)
            team_matches = [row for row in rows if team in (row["home_team"], row["away_team"])]
            results = sorted(team_matches, key=lambda row: row["kickoff_utc"], reverse=True)[:10]
            next_matches = sorted(
                {m["id"]: m for m in upcoming_by_team.get(team, [])}.values(),
                key=lambda m: m["kickoff_utc"],
            )[:5]

            result_rows = "".join(
                "<tr><td>%s</td><td>%s</td><td><b>%d:%d</b></td><td>%s</td></tr>"
                % (
                    row["kickoff_utc"].astimezone(MSK).strftime("%d.%m"),
                    html.escape(russian)
                    if row["home_team"] == team
                    else team_html(row["home_team"]),
                    int(row["home_goals"]),
                    int(row["away_goals"]),
                    team_html(row["away_team"])
                    if row["home_team"] == team
                    else html.escape(russian),
                )
                for row in results
            )

            next_rows = []
            for match in next_matches:
                rates = model.league_rates.get(match["competition_id"]) or model
                try:
                    forecast = build_forecast(
                        rates,
                        competition_id=match["competition_id"],
                        home_team=match["home_team"],
                        away_team=match["away_team"],
                        kickoff_utc=match["kickoff_utc"],
                    )
                except Exception:  # noqa: BLE001
                    continue
                next_rows.append(
                    '<tr><td>%s</td><td><a href="/match/%d/">%s — %s</a></td>'
                    "<td>П1 %.0f%% · Х %.0f%% · П2 %.0f%%</td><td>ТБ 2,5 %.0f%%</td></tr>"
                    % (
                        match["kickoff_utc"].astimezone(MSK).strftime("%d.%m %H:%M"),
                        match["id"],
                        team_html(match["home_team"]),
                        team_html(match["away_team"]),
                        forecast.p_home * 100,
                        forecast.p_draw * 100,
                        forecast.p_away * 100,
                        forecast.total[2.5] * 100,
                    )
                )

            wins = sum(
                1
                for row in results
                if (row["home_goals"] > row["away_goals"]) == (row["home_team"] == team)
                and row["home_goals"] != row["away_goals"]
            )
            draws = sum(1 for row in results if row["home_goals"] == row["away_goals"])
            losses = len(results) - wins - draws
            place = position.get(team)
            content = (
                "<h1>%s: матчи, результаты и форма</h1>" % html.escape(russian)
                + '<p class="muted">%s · сезон %s</p>' % (html.escape(name), season_label)
                + (
                    "<p>Место в таблице: <b>%d</b> · "
                    '<a href="/table/%s/">турнирная таблица</a></p>' % (place, comp_slug)
                    if place
                    else ""
                )
                + "<p>Последние 10 матчей: <b>%d</b> побед, <b>%d</b> ничьих, <b>%d</b> поражений.</p>"
                % (wins, draws, losses)
                + '<h2 class="section-title">Ближайшие матчи</h2>'
                + (
                    "<table><thead><tr><th>Время</th><th>Матч</th><th>Исход</th><th>Тотал</th>"
                    "</tr></thead><tbody>%s</tbody></table>" % "".join(next_rows)
                    if next_rows
                    else '<p class="muted">Ближайших матчей в расписании нет.</p>'
                )
                + '<h2 class="section-title">Последние результаты</h2>'
                + "<table><thead><tr><th>Дата</th><th>Хозяева</th><th>Счёт</th><th>Гости</th>"
                "</tr></thead><tbody>%s</tbody></table>"
                % result_rows
                + '<p class="muted"><a href="/schedule/%s/">Расписание %s</a> · '
                '<a href="/results/%s/">Результаты</a></p>'
                % (comp_slug, html.escape(name), comp_slug)
            )

            path = OUT / slug / "index.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                PAGE.format(
                    title="%s: расписание, результаты и форма — FOOTBALLINHO" % russian,
                    description="%s: ближайшие матчи, последние результаты, форма и место "
                    "в турнирной таблице %s. Сезон %s. 18+" % (russian, name, season_label),
                    canonical="%s/team/%s/" % (BASE_URL, slug),
                    jsonld=json.dumps(
                        {
                            "@context": "https://schema.org",
                            "@type": "SportsTeam",
                            "name": russian,
                            "sport": "Футбол",
                            "memberOf": {"@type": "SportsOrganization", "name": name},
                        },
                        ensure_ascii=False,
                    ),
                    content=content,
                    bot=BOT_URL,
                ),
                encoding="utf-8",
            )
            written += 1

    print("страниц команд: %d" % written)


if __name__ == "__main__":
    asyncio.run(build())
