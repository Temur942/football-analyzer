"""Партнёрские страницы: раздел под бренд канала.

Партнёр получает не «карточку», а страницу со своим именем, ссылкой и живыми
вероятностями — её можно дать аудитории как полезный раздел, а не рекламу.
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

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "landing" / "p"
BASE = "https://footballinho.ru"
BOT = "https://t.me/footballizator_bot"

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
<style>:root {{ --accent: {color}; }}</style>
</head>
<body>
<header class="nav">
  <a class="brand" href="/">FOOTBALLINHO</a>
  <nav>
    <a href="/line/">Беттинг-центр</a>
    <a href="/live/">Live</a>
    <a href="/schedule/">Расписание</a>
    <a href="/table/">Таблицы</a>
    <a class="cta" href="{bot}?start=ch_{slug}">Открыть бота</a>
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
    <a href="/privacy.html">Политика ПД</a> ·
    <a href="/cookies.html">Cookie</a> ·
    <a href="#" data-cookie-settings>Настройки cookie</a>
  </p>
</footer>
</body>
</html>
"""


async def build() -> None:
    pool = await db.connect(os.environ["DATABASE_URL"])
    partners = await db.partners_list(pool)
    if not partners:
        print("партнёров нет — страницы не собираются")
        await pool.close()
        return

    upcoming = await db.upcoming_matches(pool, 14)
    model = await fit_all_models(pool)

    async with pool.acquire() as conn:
        promos = await conn.fetch("SELECT code, partner_slug, days FROM promo_codes WHERE active")
    await pool.close()

    promos_by_partner: dict[str, list] = {}
    for row in promos:
        if row["partner_slug"]:
            promos_by_partner.setdefault(row["partner_slug"], []).append(row)

    prepared = []
    for match in upcoming:
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

    written = 0
    for partner in partners:
        slug = partner["slug"]
        name = partner["name"]
        color = partner["brand_color"] or "#e5320f"
        link = "%s?start=ch_%s" % (BOT, slug)

        rows = "".join(
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
            for match, forecast in prepared[:12]
        )

        promo_html = ""
        for promo in promos_by_partner.get(slug, []):
            promo_html += (
                '<div class="card"><h3>Промокод для подписчиков</h3>'
                "<p><code>%s</code> — %d дней доступа бесплатно. Активируется командой "
                "<code>/promo %s</code> в боте.</p></div>"
                % (html.escape(promo["code"]), promo["days"], html.escape(promo["code"]))
            )

        channel = (
            '<p><a href="%s">Канал партнёра</a></p>' % html.escape(partner["channel_url"])
            if partner["channel_url"]
            else ""
        )

        content = (
            '<p class="muted">Раздел партнёра</p>'
            "<h1>%s</h1>"
            % html.escape(name)
            + (channel or "")
            + "<p>Вероятности ближайших матчей по модели FOOTBALLINHO. "
            "Обновляются каждый час, бесплатно для читателей этого раздела.</p>"
            + promo_html
            + '<h2 class="section-title">Ближайшие матчи</h2>'
            + (
                "<table><thead><tr><th>Время</th><th>Матч</th><th>Исход</th><th>Тотал</th>"
                "</tr></thead><tbody>%s</tbody></table>" % rows
                if rows
                else '<p class="muted">Ближайших матчей нет.</p>'
            )
            + '<p><a class="button" href="%s">Открыть бота</a></p>' % link
            + '<p class="muted"><a href="/match-center/">Все матчи</a> · '
            '<a href="/table/">Таблицы</a> · <a href="/schedule/">Расписание</a></p>'
        )

        path = OUT / slug / "index.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            PAGE.format(
                title="%s: вероятности матчей — FOOTBALLINHO" % html.escape(name),
                description="Раздел партнёра %s: вероятности исходов и тоталов на ближайшие "
                "матчи. 18+" % name,
                canonical="%s/p/%s/" % (BASE, slug),
                color=html.escape(color),
                content=content,
                bot=BOT,
                slug=slug,
            ),
            encoding="utf-8",
        )
        written += 1

    print("партнёрских страниц: %d" % written)


if __name__ == "__main__":
    asyncio.run(build())
