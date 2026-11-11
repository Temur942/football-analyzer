"""Публикации в канал: очередь постов и сборка поста-прогноза."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import asyncpg
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from football_analyzer.analysis.forecast import MSK, Forecast, build_forecast
from football_analyzer.analysis.service import CUP_PREFIXES, fit_all_models
from football_analyzer.storage import db
from football_analyzer.teams_ru import to_russian

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


async def build_forecast_post(pool: asyncpg.Pool) -> dict | None:
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
        return {
            "text": post_text(forecast, row["name_ru"]),
            "match_id": int(row["id"]),
            "competition": str(row["name_ru"]),
            "home": to_russian(row["home_team"]),
            "away": to_russian(row["away_team"]),
            "kickoff": row["kickoff_utc"].astimezone(MSK).strftime("%d.%m, %H:%M МСК"),
            "rows": [
                ("Тотал 2,5", forecast.total[2.5]),
                ("Углы 9,5", forecast.corners[9.5]),
                ("ЖК 4,5", forecast.yellow[4.5]),
            ],
        }
    return None


def accuracy_post(report: dict) -> str:
    """Пост с публичной точностью. Закрывает главный вопрос «а оно работает?»."""
    markets = report.get("markets") or {}
    confident = (report.get("confident") or {}).get("исход") or {}
    lines = [
        "📊 <b>Точность за %d дней</b>" % report.get("window_days", 30),
        "",
        "Считаем на матчах, которых модель не видела.",
        "",
    ]
    names = {"исход": "Исход", "тотал 2,5": "Тотал 2,5", "углы 9,5": "Углы 9,5", "ЖК 4,5": "ЖК 4,5"}
    for key, title in names.items():
        value = markets.get(key)
        if not value:
            continue
        lines.append(
            "▫️ %s — <b>%.1f%%</b> (%d из %d)"
            % (title, value["rate"] * 100, value["hits"], value["n"])
        )
    if confident:
        lines += [
            "",
            "Только уверенные (≥60%% или ≤40%%):",
            "▫️ Исход — <b>%.1f%%</b> (%d из %d)"
            % (confident["rate"] * 100, confident["hits"], confident["n"]),
        ]
    lines += [
        "",
        "Показываем обе цифры: и общую, и уверенную.",
        "Бот: @footballizator_bot",
        "",
        "<i>18+. Вероятности, не гарантии.</i>",
    ]
    return "\n".join(lines)


async def publish_next(bot: object, pool: asyncpg.Pool, channel_id: str) -> bool:
    """Опубликовать следующий пост очереди. True — что-то ушло в канал."""
    row = await db.next_post(pool)
    if row is None:
        log.info("очередь публикаций пуста")
        return False

    text = row["body"]
    card_data: dict | None = None
    if row["kind"] == "forecast" or not text:
        built = await build_forecast_post(pool)
        if built is None:
            log.warning("пост-прогноз не собран: нет матчей")
            return False
        text = built["text"]
        card_data = built

    bot_url = "https://t.me/footballizator_bot"
    if card_data is not None:
        bot_url += "?start=match_%d" % card_data["match_id"]
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть бота", url=bot_url)]]
    )
    if card_data is not None:
        # Карточка с цифрами: в ленте заметнее, чем текст.
        try:
            from football_analyzer.media.card import render_forecast_card

            card_path = render_forecast_card(
                competition=card_data["competition"],
                home=card_data["home"],
                away=card_data["away"],
                kickoff=card_data["kickoff"],
                rows=card_data["rows"],
                out_path=Path("/tmp") / ("card_%d.png" % card_data["match_id"]),
            )
            caption = "⚽ <b>%s — %s</b>\n🕐 %s\n\nПолная раскладка — в боте." % (
                card_data["home"],
                card_data["away"],
                card_data["kickoff"],
            )
            await bot.send_photo(  # type: ignore[attr-defined]
                channel_id,
                FSInputFile(card_path),
                caption=caption,
                reply_markup=keyboard,
            )
            await db.mark_posted(pool, row["id"])
            log.info("опубликован пост-карточка id=%s", row["id"])
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("карточка не отправилась, отправляю текстом: %s", exc)

    # Текстовый пост: обложка с заголовком + текст в подписи (лимит 1024).
    try:
        from football_analyzer.media.card import render_text_card

        title = re.sub(r"<[^>]+>", "", text.splitlines()[0]).strip() or "Football Analyzer"
        card_path = render_text_card(
            title=title,
            label="Разбор",
            out_path=Path("/tmp") / ("card_text_%d.png" % row["id"]),
        )
        await bot.send_photo(  # type: ignore[attr-defined]
            channel_id,
            FSInputFile(card_path),
            caption=text[:1024],
            reply_markup=keyboard,
        )
        await db.mark_posted(pool, row["id"])
        log.info("опубликован текстовый пост с карточкой id=%s", row["id"])
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("обложка не отправилась, отправляю текстом: %s", exc)

    await bot.send_message(  # type: ignore[attr-defined]
        channel_id, text, reply_markup=keyboard
    )
    await db.mark_posted(pool, row["id"])
    log.info("опубликован пост id=%s", row["id"])
    return True
