"""Публикации в канал: очередь постов и сборка поста-прогноза."""

from __future__ import annotations

import logging

import asyncpg

from football_analyzer.analysis.forecast import build_forecast, render_text
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


def post_text(forecast: object, competition_name: str) -> str:
    """Короткая версия прогноза для канала — без полной раскладки."""
    full = render_text(forecast, competition_name)  # type: ignore[arg-type]
    lines = full.splitlines()
    keep = []
    for line in lines:
        # В канал — заголовок, время, исход, тотал, счёт и призыв в бота.
        if line.startswith(("⚽", "🏆", "🕐")):
            keep.append(line)
        elif line.startswith(("🎯", "🥅", "🔮")):
            keep.append(line)
            continue
        elif keep and keep[-1].startswith(("🎯", "🥅", "🔮")):
            keep.append(line)
    header = "\n".join(keep)
    return (
        header
        + "\n\nПолная раскладка — углы, карточки, голы команд — в боте за 5 часов до матча:\n"
        "@footballizator_bot\n\n<i>18+. Вероятности, не гарантии.</i>"
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

    await bot.send_message(channel_id, text)  # type: ignore[attr-defined]
    await db.mark_posted(pool, row["id"])
    log.info("опубликован пост id=%s", row["id"])
    return True
