"""Расписание матчей: сегодня, завтра и по каждому турниру.

Вордстат: кластер «расписание/календарь» — 32,4 млн показов, «сегодня» — 9,5 млн.
Страницы отвечают на прямой вопрос «что играют», а рядом — вероятности и переход
в бота.
"""

from __future__ import annotations

import asyncio
import html
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from football_analyzer.analysis.forecast import MSK, build_forecast
from football_analyzer.analysis.service import CUP_PREFIXES, fit_all_models
from football_analyzer.competitions import load_competitions
from football_analyzer.storage import db
from football_analyzer.teams_ru import to_russian

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "landing" / "schedule"
BASE_URL = "https://footballinho.ru"
BOT_URL = "https://t.me/footballizator_bot?start=ch_site"


def slugify(value: str) -> str:
    value = value.lower().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", "-", value).strip("-") or "league"


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
    <a href="/match-center/">Матч-центр</a>
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


def write_page(relative: str, *, title: str, description: str, content: str) -> None:
    path = OUT / relative / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        PAGE.format(
            title=html.escape(title),
            description=html.escape(description)[:300],
            canonical="%s/schedule/%s" % (BASE_URL, relative + "/" if relative else ""),
            content=content,
            bot=BOT_URL,
        ),
        encoding="utf-8",
    )


def match_row(match, forecast) -> str:
    kickoff = match["kickoff_utc"].astimezone(MSK)
    return (
        '<tr><td>%s</td><td>%s</td><td><a href="/match/%d/">%s — %s</a></td>'
        "<td>П1 %.0f%% · Х %.0f%% · П2 %.0f%%</td><td>%s</td></tr>"
        % (
            kickoff.strftime("%H:%M"),
            html.escape(str(match["name_ru"])),
            match["id"],
            html.escape(to_russian(match["home_team"])),
            html.escape(to_russian(match["away_team"])),
            forecast.p_home * 100,
            forecast.p_draw * 100,
            forecast.p_away * 100,
            "ТБ 2,5 %.0f%%" % (forecast.total[2.5] * 100),
        )
    )


async def build() -> None:
    pool = await db.connect(os.environ["DATABASE_URL"])
    rows = await db.upcoming_matches(pool, 14)
    model = await fit_all_models(pool)
    await pool.close()

    competitions = {getattr(c, "id", ""): getattr(c, "name_ru", "") for c in load_competitions()}

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

    today = datetime.now(MSK).date()
    tomorrow = today + timedelta(days=1)

    def day_block(day) -> str:
        day_matches = [
            (match, forecast)
            for match, forecast in prepared
            if match["kickoff_utc"].astimezone(MSK).date() == day
        ]
        if not day_matches:
            return '<p class="muted">Матчей нет.</p>'
        body = "".join(match_row(match, forecast) for match, forecast in day_matches)
        return (
            "<table><thead><tr><th>Время</th><th>Турнир</th><th>Матч</th>"
            "<th>Исход</th><th>Тотал</th></tr></thead><tbody>%s</tbody></table>" % body
        )

    # 1. Сегодня
    write_page(
        "today",
        title="Футбол сегодня: расписание матчей и вероятности | Football Analyzer",
        description="Какие футбольные матчи играют сегодня: расписание по времени МСК, "
        "вероятности исходов и тоталов на каждый матч. 18+",
        content='<h1>Футбол сегодня</h1><p class="muted">%s · время МСК</p>%s%s'
        % (
            today.strftime("%d.%m.%Y"),
            day_block(today),
            '<p><a href="/schedule/tomorrow/">Расписание на завтра →</a> · '
            '<a href="/schedule/">Все дни →</a></p>',
        ),
    )

    # 2. Завтра
    write_page(
        "tomorrow",
        title="Футбол завтра: расписание матчей и вероятности | Football Analyzer",
        description="Расписание футбольных матчей на завтра с вероятностями исходов "
        "и тоталов. Время МСК. 18+",
        content='<h1>Футбол завтра</h1><p class="muted">%s · время МСК</p>%s%s'
        % (
            tomorrow.strftime("%d.%m.%Y"),
            day_block(tomorrow),
            '<p><a href="/schedule/today/">Матчи сегодня →</a> · '
            '<a href="/schedule/">Все дни →</a></p>',
        ),
    )

    # 3. Индекс: ближайшие дни и ссылки на турниры
    by_competition: dict[str, list] = {}
    for match, forecast in prepared:
        by_competition.setdefault(match["competition_id"], []).append((match, forecast))

    cards = []
    for competition_id, items in sorted(by_competition.items(), key=lambda pair: -len(pair[1])):
        name = competitions.get(competition_id) or str(items[0][0]["name_ru"])
        cards.append(
            '<a class="card" href="/schedule/%s/"><h3>%s</h3>'
            '<p class="muted">матчей: %d</p></a>'
            % (slugify(competition_id), html.escape(name), len(items))
        )

    days = []
    for offset in range(0, 7):
        day = today + timedelta(days=offset)
        day_matches = [
            (match, forecast)
            for match, forecast in prepared
            if match["kickoff_utc"].astimezone(MSK).date() == day
        ]
        if not day_matches:
            continue
        title = day.strftime("%d.%m")
        if offset == 0:
            title += " — сегодня"
        elif offset == 1:
            title += " — завтра"
        body = "".join(match_row(match, forecast) for match, forecast in day_matches[:12])
        days.append(
            '<h2 class="section-title">%s</h2>'
            "<table><thead><tr><th>Время</th><th>Турнир</th><th>Матч</th>"
            "<th>Исход</th><th>Тотал</th></tr></thead><tbody>%s</tbody></table>" % (title, body)
        )

    write_page(
        "",
        title="Расписание футбольных матчей на неделю | Football Analyzer",
        description="Расписание матчей на неделю по всем турнирам: время МСК, "
        "вероятности исходов и тоталов. 18+",
        content="<h1>Расписание матчей</h1>"
        '<p class="muted">Ближайшая неделя, время МСК. '
        '<a href="/schedule/today/">Сегодня</a> · '
        '<a href="/schedule/tomorrow/">Завтра</a></p>'
        '<div class="grid">%s</div>%s' % ("".join(cards), "".join(days)),
    )

    # 4. По каждому турниру
    for competition_id, items in by_competition.items():
        name = competitions.get(competition_id) or str(items[0][0]["name_ru"])
        items.sort(key=lambda pair: pair[0]["kickoff_utc"])
        body = "".join(match_row(match, forecast) for match, forecast in items)
        write_page(
            slugify(competition_id),
            title="Расписание матчей %s — Football Analyzer" % name,
            description="%s: расписание ближайших матчей, время МСК и вероятности "
            "исходов. 18+" % name,
            content="<h1>Расписание матчей: %s</h1>"
            '<p class="muted">Ближайшие две недели. '
            '<a href="/table/%s/">Турнирная таблица →</a></p>'
            "<table><thead><tr><th>Время</th><th>Турнир</th><th>Матч</th>"
            "<th>Исход</th><th>Тотал</th></tr></thead><tbody>%s</tbody></table>"
            % (html.escape(name), slugify(competition_id), body),
        )

    print("расписание: матчей %d, турниров %d" % (len(prepared), len(by_competition)))


if __name__ == "__main__":
    asyncio.run(build())
