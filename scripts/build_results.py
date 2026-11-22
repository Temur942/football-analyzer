"""Результаты матчей: последние игры и сверка с прогнозом.

Вордстат: кластер «результаты/счёт» — 21,2 млн показов. Отличие от конкурентов:
рядом со счётом показываем, что говорила модель, и сбылось ли это. Это и есть
публичный журнал прогнозов, только встроенный в привычную страницу результатов.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from football_analyzer.analysis.forecast import MSK
from football_analyzer.competitions import load_competitions
from football_analyzer.storage import db
from football_analyzer.team_logos import team_html

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "landing" / "results"
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
  <a class="brand" href="/">FOOTBALLINHO</a>
  <nav>
    <a href="/line/">Беттинг-центр</a>
    <a href="/live/">Live</a>
    <a href="/schedule/">Расписание</a>
    <a href="/results/">Результаты</a>
    <a href="/table/">Таблицы</a>
    <a href="/archive/">Архив</a>
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
    path = OUT / relative / "index.html" if relative else OUT / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        PAGE.format(
            title=html.escape(title),
            description=html.escape(description)[:300],
            canonical="%s/results/%s" % (BASE_URL, relative + "/" if relative else ""),
            content=content,
            bot=BOT_URL,
        ),
        encoding="utf-8",
    )


def verdict(match: dict, payload: dict | None) -> tuple[str, bool | None]:
    """Что говорила модель по тоталу 2,5 и сбылось ли.

    Возвращает готовую разметку и признак попадания (None — прогноза нет).
    """
    if not payload:
        return "<span class='muted'>—</span>", None
    probability = float(payload.get("total", {}).get("2.5") or 0)
    if probability <= 0:
        return "<span class='muted'>—</span>", None
    goals = int(match["home_goals"] or 0) + int(match["away_goals"] or 0)
    predicted_over = probability >= 0.5
    hit = (goals > 2.5) == predicted_over
    line = "ТБ 2,5" if predicted_over else "ТМ 2,5"
    markup = "<span class='chip'>%s %.0f%% · %s</span>" % (
        line,
        probability * 100,
        "✅" if hit else "❌",
    )
    return markup, hit


def row(match: dict, payload: dict | None) -> str:
    kickoff = match["kickoff_utc"].astimezone(MSK)
    home_goals = match["home_goals"] if match["home_goals"] is not None else 0
    away_goals = match["away_goals"] if match["away_goals"] is not None else 0
    return (
        "<tr><td>%s</td><td>%s</td>"
        '<td><a href="/match/%d/">%s — %s</a></td>'
        "<td><b>%d:%d</b></td><td>%s</td></tr>"
        % (
            kickoff.strftime("%d.%m"),
            html.escape(str(match["name_ru"])),
            match["id"],
            team_html(match["home_team"]),
            team_html(match["away_team"]),
            home_goals,
            away_goals,
            verdict(match, payload)[0],
        )
    )


async def build() -> None:
    pool = await db.connect(os.environ["DATABASE_URL"])
    competitions = {getattr(c, "id", ""): getattr(c, "name_ru", "") for c in load_competitions()}

    since = datetime.now(MSK) - timedelta(days=14)
    async with pool.acquire() as conn:
        matches = await conn.fetch(
            """
            SELECT m.id, m.competition_id, c.name_ru, m.home_team, m.away_team,
                   m.home_goals, m.away_goals, m.kickoff_utc
              FROM matches m
              JOIN competitions c ON c.id = m.competition_id
             WHERE m.status = 'finished' AND m.home_goals IS NOT NULL
               AND m.kickoff_utc >= $1
             ORDER BY m.kickoff_utc DESC
            """,
            since,
        )
        prediction_rows = await conn.fetch(
            "SELECT match_id, payload FROM predictions WHERE created_at >= $1",
            since - timedelta(days=7),
        )
    await pool.close()

    predictions = {int(row["match_id"]): row["payload"] for row in prediction_rows}
    if isinstance(next(iter(predictions.values()), None), str):
        predictions = {key: json.loads(value) for key, value in predictions.items()}

    prepared = [dict(row) for row in matches]
    for match in prepared:
        payload = predictions.get(int(match["id"]))
        if isinstance(payload, str):
            payload = json.loads(payload)
        match["payload"] = payload

    hits = 0
    checked = 0
    for match in prepared:
        _, hit = verdict(match, match["payload"])
        if hit is None:
            continue
        checked += 1
        if hit:
            hits += 1

    by_competition: dict[str, list] = {}
    for match in prepared:
        by_competition.setdefault(match["competition_id"], []).append(match)

    cards = [
        '<a class="card" href="/results/%s/"><h3>%s</h3><p class="muted">матчей: %d</p></a>'
        % (
            slugify(competition_id),
            html.escape(competitions.get(competition_id, competition_id)),
            len(items),
        )
        for competition_id, items in sorted(by_competition.items(), key=lambda pair: -len(pair[1]))
    ]

    # Индекс: последние дни
    days = []
    for offset in range(0, 14):
        day = (datetime.now(MSK) - timedelta(days=offset)).date()
        day_matches = [
            match for match in prepared if match["kickoff_utc"].astimezone(MSK).date() == day
        ]
        if not day_matches:
            continue
        title = day.strftime("%d.%m.%Y")
        if offset == 0:
            title += " — сегодня"
        elif offset == 1:
            title += " — вчера"
        body = "".join(row(match, match["payload"]) for match in day_matches)
        days.append(
            '<h2 class="section-title">%s</h2>'
            "<table><thead><tr><th>Дата</th><th>Турнир</th><th>Матч</th><th>Счёт</th>"
            "<th>Прогноз</th></tr></thead><tbody>%s</tbody></table>" % (title, body)
        )

    summary = (
        '<p class="muted">Прогнозов в проверке: <b>%d</b> · сбылось: <b>%d</b></p>'
        % (checked, hits)
        if checked
        else '<p class="muted">Прогнозов за период пока нет.</p>'
    )

    results_ld = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "ItemList",
            "name": "Результаты футбольных матчей",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": index,
                    "name": "%s — %s" % (m["home_team"], m["away_team"]),
                }
                for index, m in enumerate(prepared[:30], start=1)
            ],
        },
        ensure_ascii=False,
    )
    write_page(
        "",
        title="Результаты футбольных матчей и проверка прогнозов | FOOTBALLINHO",
        description="Результаты футбольных матчей за две недели с проверкой прогноза: "
        "что говорила модель и сбылось ли. 18+",
        content='<script type="application/ld+json">%s</script>'
        % results_ld
        + "<h1>Результаты матчей</h1>%s"
        '<div class="grid">%s</div>%s' % (summary, "".join(cards), "".join(days)),
    )

    for competition_id, items in by_competition.items():
        name = competitions.get(competition_id) or str(items[0]["name_ru"])
        body = "".join(row(match, match["payload"]) for match in items)
        write_page(
            slugify(competition_id),
            title="Результаты матчей %s — FOOTBALLINHO" % name,
            description="Результаты матчей %s за две недели: счёт и проверка прогноза "
            "модели. 18+" % name,
            content="<h1>Результаты матчей: %s</h1>"
            '<p class="muted"><a href="/schedule/%s/">Расписание →</a> · '
            '<a href="/table/%s/">Таблица →</a></p>'
            "<table><thead><tr><th>Дата</th><th>Турнир</th><th>Матч</th><th>Счёт</th>"
            "<th>Прогноз</th></tr></thead><tbody>%s</tbody></table>"
            % (html.escape(name), slugify(competition_id), slugify(competition_id), body),
        )

    print(
        "результаты: матчей %d, турниров %d, прогнозов в проверке %d"
        % (len(prepared), len(by_competition), checked)
    )


if __name__ == "__main__":
    asyncio.run(build())
