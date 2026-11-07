"""Рассылка прогнозов: за N часов до матча, один раз на матч и подписчика.

Ключевое свойство — идемпотентность. Запись в `deliveries` имеет первичный
ключ (match_id, chat_id), поэтому повторный проход, перезапуск или два
процесса одновременно не пришлют прогноз дважды: второй просто получит
конфликт и пропустит.

Режим dry_run существует не «для галочки»: пока нет выхода в Telegram,
весь конвейер работает и проверяется по базе, а реальная отправка
включается одной командой.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import asyncpg

from football_analyzer.analysis.forecast import build_forecast, render_text
from football_analyzer.analysis.service import fit_all_models
from football_analyzer.storage import db

log = logging.getLogger(__name__)

# Версия модели: растёт при изменении расчётов. Старые прогнозы остаются
# в базе с прежней версией — можно сравнить «было/стало».
MODEL_VERSION = "v1"

SendFunc = Callable[[int, str], Awaitable[None]]


@dataclass
class DeliveryReport:
    due: int = 0
    dry_run: int = 0
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    texts: list[str] = field(default_factory=list)


def forecast_payload(forecast: object) -> str:
    """Сериализовать прогноз для хранения в predictions.payload."""
    return json.dumps(
        {
            "competition_id": forecast.competition_id,  # type: ignore[attr-defined]
            "home_team": forecast.home_team,  # type: ignore[attr-defined]
            "away_team": forecast.away_team,  # type: ignore[attr-defined]
            "kickoff_utc": forecast.kickoff_utc.isoformat(),  # type: ignore[attr-defined]
            "p_home": round(forecast.p_home, 4),  # type: ignore[attr-defined]
            "p_draw": round(forecast.p_draw, 4),  # type: ignore[attr-defined]
            "p_away": round(forecast.p_away, 4),  # type: ignore[attr-defined]
            "total": {str(k): round(v, 4) for k, v in forecast.total.items()},  # type: ignore[attr-defined]
            "first_half": {
                str(k): round(v, 4)
                for k, v in forecast.first_half.items()  # type: ignore[attr-defined]
            },
            "corners": {str(k): round(v, 4) for k, v in forecast.corners.items()},  # type: ignore[attr-defined]
            "yellow": {str(k): round(v, 4) for k, v in forecast.yellow.items()},  # type: ignore[attr-defined]
        },
        ensure_ascii=False,
    )


async def deliver_due(
    pool: asyncpg.Pool,
    *,
    lead_hours: int = 5,
    dry_run: bool = True,
    send: SendFunc | None = None,
    max_matches: int = 200,
    recipients: list[int] | None = None,
) -> DeliveryReport:
    """Найти матчи в окне «за lead_hours» и разослать прогнозы.

    dry_run=True — ничего не отправляем, но пишем в журнал статус dry_run.
    send — функция реальной отправки (chat_id, text); нужна при dry_run=False.
    """
    report = DeliveryReport()
    rows = await db.due_matches(pool, lead_hours)
    if not rows:
        return report
    rows = rows[:max_matches]
    report.due = len(rows)

    if recipients:
        # Явный список получателей (например, админы до запуска продаж).
        chats = [int(chat_id) for chat_id in recipients]
    else:
        subscribers = await db.active_subscribers(pool)
        chats = [int(row["chat_id"]) for row in subscribers]
    if not chats:
        # Подписчиков пока нет: пишем прогон в журнал на служебный адрес,
        # чтобы конвейер был проверяемым до запуска продаж.
        chats = [0]

    if not dry_run and send is None:
        raise RuntimeError("для реальной отправки нужна функция send")

    model = await fit_all_models(pool)

    for row in rows:
        competition_id = row["competition_id"]
        rates = (
            model
            if competition_id.startswith(("uefa-", "int-"))
            else model.league_rates.get(competition_id)
        )
        if (
            rates is None
            or row["home_team"] not in model.team_league
            or row["away_team"] not in model.team_league
        ):
            report.skipped += 1
            continue

        forecast = build_forecast(
            rates,  # type: ignore[arg-type]
            competition_id=competition_id,
            home_team=row["home_team"],
            away_team=row["away_team"],
            kickoff_utc=row["kickoff_utc"],
        )
        text = render_text(forecast, row["name_ru"])
        await db.save_prediction(pool, row["id"], MODEL_VERSION, forecast_payload(forecast))

        for chat_id in chats:
            if dry_run:
                inserted = await db.try_insert_delivery(pool, row["id"], chat_id, "dry_run")
                if inserted:
                    report.dry_run += 1
                    report.texts.append(text)
                else:
                    report.skipped += 1
                continue

            assert send is not None
            try:
                await send(chat_id, text)
            except Exception as exc:  # noqa: BLE001
                report.failed += 1
                await db.try_insert_delivery(pool, row["id"], chat_id, "failed", str(exc)[:300])
                log.warning("отправка не удалась (чат %s, матч %s): %s", chat_id, row["id"], exc)
                continue
            inserted = await db.try_insert_delivery(pool, row["id"], chat_id, "sent")
            if inserted:
                report.sent += 1
            else:
                report.skipped += 1

    return report
