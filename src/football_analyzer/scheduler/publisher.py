"""Публикации в канал: очередь постов и сборка поста-прогноза."""

from __future__ import annotations

import logging

import asyncpg
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from football_analyzer.analysis.forecast import MSK, Forecast, build_forecast
from football_analyzer.analysis.service import CUP_PREFIXES, fit_all_models
from football_analyzer.storage import db

log = logging.getLogger(__name__)

# Лиги, из которых берём матч для поста-прогноза (сначала самые популярные).
PREFERRED_LEAGUES = (
    "eng-premier-league",
    "esp-la-liga",
    "ita-serie-a",
    "ger-bundesliga",
    "fra-ligue-1",
    "uefa-champions-league",
    "uefa-europa-league",
)


def post_text(forecast: Forecast, competition_name: str) -> str:
    """Короткий пост для канала: главное, без полной раскладки.

    Собираем явно из чисел прогноза, а не обрезаем длинный текст: обрезка
    уже один раз оставила в «Исходе» только строку про хозяев.
    """
    kickoff = forecast.kickoff_utc.astimezone(MSK)

    def pct(value: float) -> str:
        return "%d%%" % round(value * 100)

    def line(value: float) -> str:
        return ("%.1f" % value).replace(".", ",")

    total = " · ".join(
        "ТБ %s — <b>%s</b>" % (line(value_line), pct(probability))
        for value_line, probability in forecast.total.items()
    )
    scores = " · ".join(
        "<b>%d:%d</b> %s" % (home, away, pct(probability))
        for home, away, probability in forecast.scores
    )
    return "\n".join(
        [
            "⚽ <b>%s — %s</b>" % (forecast.home_team, forecast.away_team),
            "🏆 %s" % competition_name,
            "🕐 %s МСК" % kickoff.strftime("%d.%m, %H:%M"),
            "",
            "🎯 <b>Исход:</b> П1 %s · Х %s · П2 %s"
            % (pct(forecast.p_home), pct(forecast.p_draw), pct(forecast.p_away)),
            "🥅 <b>Тотал:</b> " + total,
            "🔮 <b>Счёт:</b> " + scores,
            "",
            "Полная раскладка — углы, карточки, голы команд — в боте за 5 часов до матча:",
            "@footballizator_bot",
            "",
            "<i>18+. Вероятности, не гарантии.</i>",
        ]
    )


async def build_forecast_post(pool: asyncpg.Pool) -> str | None:
    """Пост-прогноз по ближайшему матчу популярной лиги."""
    upcoming = await db.upcoming_matches(pool, 2)
    if not upcoming:
        return None
    # Сначала приоритетные лиги, иначе любой матч.
    ordered = sorted(
        upcoming,
        key=lambda row: (
            PREFERRED_LEAGUES.index(row["competition_id"])
            if row["competition_id"] in PREFERRED_LEAGUES
            else len(PREFERRED_LEAGUES)
        ),
    )
    model = await fit_all_models(pool)
    for row in ordered:
        competition_id = row["competition_id"]
        rates = (
            model
            if competition_id.startswith(CUP_PREFIXES)
            else model.league_rates.get(competition_id)
        )
        if rates is None:
            continue
        if (
            model.team_league.get(row["home_team"]) is None
            or model.team_league.get(row["away_team"]) is None
        ):
            continue
        forecast = build_forecast(
            rates,  # type: ignore[arg-type]
            competition_id=competition_id,
            home_team=row["home_team"],
            away_team=row["away_team"],
            kickoff_utc=row["kickoff_utc"],
        )
        return post_text(forecast, row["name_ru"])
    return None


async def publish_next(bot: object, pool: asyncpg.Pool, channel_id: str) -> bool:
    """Опубликовать следующий пост очереди. True — что-то ушло в канал."""
    row = await db.next_post(pool)
    if row is None:
        log.info("очередь публикаций пуста")
        return False

    text = row["body"]
    if row["kind"] == "forecast" or not text:
        text = await build_forecast_post(pool)
        if text is None:
            log.warning("пост-прогноз не собран: нет матчей")
            return False

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть бота", url="https://t.me/footballizator_bot")]
        ]
    )
    await bot.send_message(  # type: ignore[attr-defined]
        channel_id, text, reply_markup=keyboard
    )
    await db.mark_posted(pool, row["id"])
    log.info("опубликован пост id=%s", row["id"])
    return True
