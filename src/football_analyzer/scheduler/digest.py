"""Еженедельная сводка подписчикам: что будет на неделе.

Смысл — вернуть человека в бота без рекламы: напомнить, что прогнозы приходят
сами, и показать ближайшие интересные матчи.
"""

from __future__ import annotations

import logging

import asyncpg

from football_analyzer.analysis.forecast import MSK
from football_analyzer.storage import db

log = logging.getLogger(__name__)

# Лиги, которые интересны аудитории в первую очередь.
TOP_LEAGUES = (
    "uefa-champions-league",
    "eng-premier-league",
    "esp-la-liga",
    "ita-serie-a",
    "ger-bundesliga",
    "fra-ligue-1",
    "uefa-europa-league",
)


async def build_digest(pool: asyncpg.Pool) -> str | None:
    """Текст недельной сводки. None — если матчей на неделе нет."""
    rows = await db.upcoming_matches(pool, 7)
    if not rows:
        return None

    def priority(row: asyncpg.Record) -> int:
        competition = row["competition_id"]
        if competition in TOP_LEAGUES:
            return TOP_LEAGUES.index(competition)
        return len(TOP_LEAGUES)

    top = sorted(rows, key=lambda row: (priority(row), row["kickoff_utc"]))[:3]
    lines = [
        "📅 <b>Неделя в футболе</b>",
        "",
        "На ближайшие 7 дней в базе <b>%d матчей</b>." % len(rows),
        "",
        "<b>Интересное</b>",
    ]
    for row in top:
        kickoff = row["kickoff_utc"].astimezone(MSK)
        lines.append(
            "▫️ %s %s — %s" % (kickoff.strftime("%d.%m"), row["home_team"], row["away_team"])
        )
    lines += [
        "",
        "Прогнозы приходят автоматически за 5 часов до матча — ничего искать не нужно.",
        "",
        "Бот: @footballizator_bot",
        "",
        "<i>18+. Вероятности, не гарантии.</i>",
    ]
    return "\n".join(lines)


async def send_digest(bot: object, pool: asyncpg.Pool) -> int:
    """Разослать сводку активным подписчикам. Возвращает число отправок."""
    text = await build_digest(pool)
    if text is None:
        log.info("дайджест: матчей на неделе нет")
        return 0
    sent = 0
    for row in await db.active_subscribers(pool):
        try:
            await bot.send_message(int(row["chat_id"]), text)  # type: ignore[attr-defined]
            sent += 1
        except Exception as exc:  # noqa: BLE001
            log.error("дайджест не доставлен %s: %s", row["chat_id"], exc)
    log.info("дайджест отправлен: %d", sent)
    return sent
