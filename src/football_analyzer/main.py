"""Точка входа: Telegram-бот (aiogram) + планировщик (APScheduler).

Статус: КАРКАС. Бизнес-логики здесь нет — только сборка компонентов.
Задания планировщика появятся, когда будут решены открытые вопросы из AGENTS.md
(база, источник расписания, формат прогноза).
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from football_analyzer import __version__
from football_analyzer.bot import handlers
from football_analyzer.bot.access import AccessMiddleware
from football_analyzer.config import get_settings
from football_analyzer.scheduler.delivery import deliver_due
from football_analyzer.scheduler.publisher import publish_next
from football_analyzer.storage import db

log = logging.getLogger(__name__)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    log.info(
        "football-analyzer %s стартует: env=%s, окно прогноза=%d ч",
        __version__,
        settings.app_env,
        settings.forecast_lead_hours,
    )

    bot = Bot(
        token=settings.require_bot_token(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    # База не обязательна для интерфейса: без неё бот отвечает, но не
    # показывает прогнозы. Так проще проверять интерфейс на машине без БД.
    # База нужна для прогнозов и подписок, но её недоступность не должна
    # ронять бота: интерфейс и закрытая бета работают и без неё.
    pool = None
    if settings.database_url:
        try:
            pool = await db.connect(settings.require_database_url())
            log.info("подключение к базе установлено")
        except Exception as exc:  # noqa: BLE001
            log.error("база недоступна, работаем без неё: %s", exc)

    dispatcher = Dispatcher(pool=pool)
    # Доступ закрыт для всех, кроме админов и подписчиков (до подключения оплаты).
    dispatcher.message.middleware(AccessMiddleware(pool))
    dispatcher.callback_query.middleware(AccessMiddleware(pool))
    dispatcher.include_router(handlers.router)
    log.info("закрытая бета: %s, админов: %d", settings.closed_beta, len(settings.admin_ids))

    scheduler = AsyncIOScheduler(timezone="UTC")

    async def deliver_to_chat(chat_id: int, text: str) -> None:
        await bot.send_message(chat_id, text)

    async def send_due_forecasts() -> None:
        """Разослать прогнозы админам за N часов до матча.

        Пока продаж нет, получатели — только админы. Права и идемпотентность
        обеспечивает журнал deliveries: второй раз тот же матч не уйдёт.
        """
        if pool is None or not settings.admin_ids:
            return
        try:
            report = await deliver_due(
                pool,
                lead_hours=settings.forecast_lead_hours,
                dry_run=False,
                send=deliver_to_chat,
                recipients=settings.admin_ids,
            )
            if report.sent:
                log.info("прогнозов отправлено: %d", report.sent)
            if report.failed:
                log.warning("не отправлено: %d", report.failed)
        except Exception as exc:  # noqa: BLE001
            log.error("рассылка не удалась: %s", exc)

    scheduler.add_job(send_due_forecasts, "interval", minutes=10, id="deliver")

    async def publish_to_channel() -> None:
        """Пост в канал: 10:00 и 18:00 МСК."""
        if pool is None or not settings.channel_id:
            return
        try:
            await publish_next(bot, pool, settings.channel_id)
        except Exception as exc:  # noqa: BLE001
            log.error("публикация в канал не удалась: %s", exc)

    scheduler.add_job(
        publish_to_channel,
        "cron",
        hour=10,
        minute=0,
        timezone="Europe/Moscow",
        id="publish_morning",
    )
    scheduler.add_job(
        publish_to_channel,
        "cron",
        hour=18,
        minute=0,
        timezone="Europe/Moscow",
        id="publish_evening",
    )
    scheduler.start()

    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        if pool is not None:
            await pool.close()


def run() -> None:
    """Синхронная обёртка для консольной команды football-analyzer."""
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("остановлено вручную")


if __name__ == "__main__":
    run()
