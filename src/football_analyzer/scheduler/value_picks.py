"""Выжимка для администратора: линии с коэффициентом от 1,7.

Что это значит: модель оценивает вероятность, обратная величина — «честный
коэффициент». Линии с 1/p ≥ 1.7 — не самые очевидные, но и не лотерея:
вероятность от 25% до 59%. Именно там обычно появляется интересная цена.
Это НЕ коэффициенты букмекера, а оценка модели — в тексте так и написано.
"""

from __future__ import annotations

import logging

import asyncpg

from football_analyzer.analysis.forecast import MSK, build_forecast
from football_analyzer.analysis.service import CUP_PREFIXES, fit_all_models
from football_analyzer.storage import db
from football_analyzer.teams_ru import to_russian

log = logging.getLogger(__name__)

MIN_ODDS = 1.7
MAX_ODDS = 4.0


def collect_lines(forecast) -> list[tuple[str, float, float]]:
    """Линии, где «честный коэффициент» модели попадает в диапазон 1,7–4,0."""
    candidates: list[tuple[str, float]] = [
        ("П1", forecast.p_home),
        ("Ничья", forecast.p_draw),
        ("П2", forecast.p_away),
    ]
    for line, probability in forecast.total.items():
        candidates.append(("ТБ %s" % str(line).replace(".", ","), probability))
        candidates.append(("ТМ %s" % str(line).replace(".", ","), 1 - probability))
    for line, probability in forecast.first_half.items():
        candidates.append(("ТБ %s в 1-м тайме" % str(line).replace(".", ","), probability))
    for line, probability in forecast.home_goals.items():
        candidates.append(("ИТ хозяев ТБ %s" % str(line).replace(".", ","), probability))
    for line, probability in forecast.away_goals.items():
        candidates.append(("ИТ гостей ТБ %s" % str(line).replace(".", ","), probability))
    for line, probability in forecast.corners.items():
        candidates.append(("углы ТБ %s" % str(line).replace(".", ","), probability))
        candidates.append(("углы ТМ %s" % str(line).replace(".", ","), 1 - probability))
    for line, probability in forecast.yellow.items():
        candidates.append(("ЖК ТБ %s" % str(line).replace(".", ","), probability))
        candidates.append(("ЖК ТМ %s" % str(line).replace(".", ","), 1 - probability))

    result: list[tuple[str, float, float]] = []
    for name, probability in candidates:
        if probability <= 0:
            continue
        odds = 1 / probability
        if MIN_ODDS <= odds <= MAX_ODDS and probability >= 0.25:
            result.append((name, probability, odds))
    return sorted(result, key=lambda item: -item[1])


async def send_value_picks(
    bot: object,
    pool: asyncpg.Pool,
    *,
    lead_hours: int = 5,
    recipients: list[int],
) -> int:
    """Отправить админам линии 1,7+ по матчам, входящим в окно."""
    due = await db.due_matches(pool, lead_hours)
    if not due:
        return 0
    model = await fit_all_models(pool)
    sent = 0

    due_by_id = {int(row["id"]): row for row in due}
    for chat_id in recipients:
        already = await db.value_alerts_for(pool, chat_id, list(due_by_id))
        for match_id, row in due_by_id.items():
            if match_id in already:
                continue
            competition = row["competition_id"]
            rates = (
                model
                if competition.startswith(CUP_PREFIXES)
                else model.league_rates.get(competition)
            )
            if rates is None:
                continue
            try:
                forecast = build_forecast(
                    rates,  # type: ignore[arg-type]
                    competition_id=competition,
                    home_team=row["home_team"],
                    away_team=row["away_team"],
                    kickoff_utc=row["kickoff_utc"],
                )
            except Exception as exc:  # noqa: BLE001
                log.error("выжимка: прогноз не посчитан для %s: %s", match_id, exc)
                continue
            lines = collect_lines(forecast)
            if not lines:
                continue
            kickoff = row["kickoff_utc"].astimezone(MSK).strftime("%d.%m, %H:%M")
            body = "\n".join(
                "▫️ %s — <b>%.0f%%</b> (коэф. модели %.2f)" % (name, probability * 100, odds)
                for name, probability, odds in lines[:6]
            )
            text = (
                "🎯 <b>%s — %s</b>\n🕐 %s МСК · %s\n\n%s\n\n"
                "<i>Коэффициенты — оценка модели (1/вероятность), не линия букмекера. 18+</i>"
                % (
                    to_russian(row["home_team"]),
                    to_russian(row["away_team"]),
                    kickoff,
                    row["name_ru"],
                    body,
                )
            )
            try:
                await bot.send_message(chat_id, text)  # type: ignore[attr-defined]
                await db.mark_value_alert(pool, match_id, chat_id)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                log.error("выжимка не отправлена %s: %s", chat_id, exc)
    return sent
