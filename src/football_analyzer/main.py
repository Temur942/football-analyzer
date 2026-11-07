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
    pool = None
    if settings.database_url:
        pool = await db.connect(settings.require_database_url())
        log.info("подключение к базе установлено")

    dispatcher = Dispatcher(pool=pool)
    # Доступ закрыт для всех, кроме админов и подписчиков (до подключения оплаты).
    dispatcher.message.middleware(AccessMiddleware(pool))
    dispatcher.callback_query.middleware(AccessMiddleware(pool))
    dispatcher.include_router(handlers.router)
    log.info("закрытая бета: %s, админов: %d", settings.closed_beta, len(settings.admin_ids))

    scheduler = AsyncIOScheduler(timezone="UTC")
    # TODO(AGENTS.md §10): добавить задание «проверить расписание и отправить
    # прогнозы за forecast_lead_hours до матча» после выбора базы и источника.
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
