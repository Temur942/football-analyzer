"""Хабы под самые крупные запросы: «футбол россии» и «футбол 2026 2027».

4,6 млн и 2,9 млн показов по Вордстату — больше, чем любая отдельная лига.
Страница-хаб собирает таблицу, расписание и результаты выбранного направления
и раздаёт ссылки на детальные страницы.
"""

from __future__ import annotations

import asyncio
import html
import os
import re
from pathlib import Path

from football_analyzer.analysis.forecast import MSK, build_forecast
from football_analyzer.analysis.service import fit_all_models
from football_analyzer.competitions import load_competitions
from football_analyzer.storage import db
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


def slugify(value: str) -> str:
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
    rows = []
    for team, values in stats.items():
        rows.append(
            {
                **values,
                "team": team,
                "points": values["won"] * 3 + values["drawn"],
                "diff": values["gf"] - values["ga"],
            }
        )
    rows.sort(key=lambda item: (-item["points"], -item["diff"], -item["gf"]))
    return rows


async def main() -> None:
    pool = await db.connect(os.environ["DATABASE_URL"])
    competitions = load_competitions()
    model = await fit_all_models(pool)
    upcoming = await db.upcoming_matches(pool, 14)

    async with pool.acquire() as conn:
        latest_season = await conn.fetchval("SELECT max(season) FROM matches")
        finished = await conn.fetch(
            "SELECT competition_id, home_team, away_team, home_goals, away_goals"
            " FROM matches WHERE season = $1 AND status = 'finished'"
            " AND home_goals IS NOT NULL",
            latest_season,
        )
    await pool.close()

    season_label = latest_season.replace("-", "/")

    # --- 1. Футбол России ---
    rpl = [dict(row) for row in finished if row["competition_id"] == "rus-premier-league"]
    table = build_table(rpl)
    rows = "".join(
        "<tr><td>%d</td><td>%s</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td>"
        "<td>%d:%d</td><td>%d</td></tr>"
        % (
            index,
            html.escape(to_russian(item["team"])),
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

    rpl_upcoming = [row for row in upcoming if row["competition_id"] == "rus-premier-league"][:6]
    next_rows = []
    for match in rpl_upcoming:
        rates = model.league_rates.get("rus-premier-league")
        if rates is None:
            continue
        try:
            forecast = build_forecast(
                rates,
                competition_id="rus-premier-league",
                home_team=match["home_team"],
                away_team=match["away_team"],
                kickoff_utc=match["kickoff_utc"],
            )
        except Exception:  # noqa: BLE001
            continue
        kickoff = match["kickoff_utc"].astimezone(MSK)
        next_rows.append(
            '<tr><td>%s</td><td><a href="/match/%d/">%s — %s</a></td>'
            "<td>П1 %.0f%% · Х %.0f%% · П2 %.0f%%</td><td>%s</td></tr>"
            % (
                kickoff.strftime("%d.%m %H:%M"),
                match["id"],
                html.escape(to_russian(match["home_team"])),
                html.escape(to_russian(match["away_team"])),
                forecast.p_home * 100,
                forecast.p_draw * 100,
                forecast.p_away * 100,
                "ТБ 2,5 %.0f%%" % (forecast.total[2.5] * 100),
            )
        )

    russia = (
        "<h1>Футбол России %s</h1>"
        % season_label
        + '<p class="muted">РПЛ: турнирная таблица, ближайшие матчи и вероятности. '
        "Данные обновляются автоматически.</p>"
        '<h2 class="section-title">Турнирная таблица РПЛ</h2>'
        "<table><thead><tr><th>#</th><th>Команда</th><th>И</th><th>В</th><th>Н</th>"
        "<th>П</th><th>Голы</th><th>О</th></tr></thead><tbody>%s</tbody></table>"
        '<p class="muted"><a href="/table/rus-premier-league/">Полная таблица →</a> · '
        '<a href="/schedule/rus-premier-league/">Расписание →</a> · '
        '<a href="/results/rus-premier-league/">Результаты →</a></p>'
        % rows
        + '<h2 class="section-title">Ближайшие матчи РПЛ</h2>'
        + (
            "<table><thead><tr><th>Время</th><th>Матч</th><th>Исход</th><th>Тотал</th>"
            "</tr></thead><tbody>%s</tbody></table>" % "".join(next_rows)
            if next_rows
            else '<p class="muted">Ближайших матчей в расписании нет.</p>'
        )
        + '<h2 class="section-title">Что это за страница</h2>'
        "<p>Мы собираем российский футбол так же, как европейские лиги: сила команд, "
        "ожидаемые голы, вероятности исходов, углов и карточек. Прогноз приходит в бот "
        "за пять часов до матча.</p>"
    )
    (OUT / "russia" / "index.html").parent.mkdir(parents=True, exist_ok=True)
    (OUT / "russia" / "index.html").write_text(
        PAGE.format(
            title="Футбол России %s: таблица РПЛ, расписание и вероятности" % season_label,
            description="Футбол России %s: турнирная таблица РПЛ, ближайшие матчи, "
            "вероятности исходов и тоталов. 18+" % season_label,
            canonical="%s/russia/" % BASE_URL,
            content=russia,
            bot=BOT_URL,
        ),
        encoding="utf-8",
    )

    # --- 2. Сезон 2026/2027 ---
    cards = []
    counts: dict[str, int] = {}
    for row in upcoming:
        counts[row["competition_id"]] = counts.get(row["competition_id"], 0) + 1
    for competition in competitions:
        competition_id = getattr(competition, "id", "")
        if not competition_id:
            continue
        name = getattr(competition, "name_ru", competition_id)
        slug = slugify(competition_id)
        links = ['<a href="/table/%s/">таблица</a>' % slug]
        if counts.get(competition_id):
            links.append('<a href="/schedule/%s/">расписание</a>' % slug)
        links.append('<a href="/match-center/%s/">матчи</a>' % slug)
        cards.append(
            '<div class="card"><h3>%s</h3><p class="muted">матчей впереди: %d</p>'
            "<p>%s</p></div>"
            % (html.escape(name), counts.get(competition_id, 0), " · ".join(links))
        )

    season = (
        "<h1>Футбол %s: сезон</h1>"
        % season_label
        + '<p class="muted">Все турниры сезона %s: таблицы, расписание, результаты '
        "и вероятности. Каждая страница обновляется автоматически.</p>"
        % season_label
        + '<div class="grid">%s</div>' % "".join(cards)
        + '<h2 class="section-title">С чего начать</h2>'
        '<p><a href="/schedule/today/">Матчи сегодня</a> · '
        '<a href="/schedule/tomorrow/">Завтра</a> · '
        '<a href="/results/">Результаты</a> · '
        '<a href="/table/">Все таблицы</a></p>'
    )
    (OUT / "season" / latest_season / "index.html").parent.mkdir(parents=True, exist_ok=True)
    (OUT / "season" / latest_season / "index.html").write_text(
        PAGE.format(
            title="Футбол %s: расписание, таблицы и результаты сезона" % season_label,
            description="Сезон %s в футболе: все турниры, турнирные таблицы, расписание "
            "матчей и результаты. 18+" % season_label,
            canonical="%s/season/%s/" % (BASE_URL, latest_season),
            content=season,
            bot=BOT_URL,
        ),
        encoding="utf-8",
    )
    print("хабы: /russia/ и /season/%s/" % latest_season)


if __name__ == "__main__":
    asyncio.run(main())
