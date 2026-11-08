"""Точка входа: Telegram-бот (aiogram) + планировщик (APScheduler).

Статус: КАРКАС. Бизнес-логики здесь нет — только сборка компонентов.
Задания планировщика появятся, когда будут решены открытые вопросы из AGENTS.md
(база, источник расписания, формат прогноза).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from football_analyzer import __version__
from football_analyzer.bot import handlers
from football_analyzer.bot.access import AccessMiddleware
from football_analyzer.config import get_settings
from football_analyzer.scheduler.delivery import deliver_due
from football_analyzer.scheduler.digest import send_digest
from football_analyzer.scheduler.publisher import accuracy_post, publish_next
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

    if pool is not None:
        # Отметка «жив» сразу: иначе мониторинг считает бота мёртвым первые 10 минут.
        try:
            await db.beat(pool)
        except Exception as exc:  # noqa: BLE001
            log.error("не удалось записать стартовый пульс: %s", exc)

    dispatcher = Dispatcher(pool=pool)
    # Доступ закрыт для всех, кроме админов и подписчиков (до подключения оплаты).
    dispatcher.message.middleware(AccessMiddleware(pool))
    dispatcher.callback_query.middleware(AccessMiddleware(pool))
    dispatcher.include_router(handlers.router)
    log.info("закрытая бета: %s, админов: %d", settings.closed_beta, len(settings.admin_ids))

    scheduler = AsyncIOScheduler(timezone="UTC")

    # Сбои подряд: одиночная ошибка — шум, три подряд — уже событие для канала.
    failure_streak: dict[str, int] = {"delivery": 0, "publish": 0}
    last_channel_alert: dict[str, float] = {}
    CHANNEL_ALERT_COOLDOWN_SECONDS = 6 * 3600
    CHANNEL_ALERT_THRESHOLD = 3

    async def deliver_to_chat(chat_id: int, text: str) -> None:
        # Небольшая пауза: при сотнях подписчиков Telegram начинает отклонять
        # частые запросы, а мы не хотим терять прогнозы из-за темпа.
        await asyncio.sleep(0.05)
        await bot.send_message(chat_id, text)

    async def notify_channel_failure(kind: str, exc: Exception) -> None:
        """Написать в канал, если сбои повторяются, но не чаще раза в 6 часов.

        Одиночный сбой не повод пугать подписчиков; три подряд — честнее
        сказать «работаем», чем молчать.
        """
        if not settings.channel_id:
            return
        if failure_streak.get(kind, 0) < CHANNEL_ALERT_THRESHOLD:
            return
        now = time.time()
        if now - last_channel_alert.get(kind, 0) < CHANNEL_ALERT_COOLDOWN_SECONDS:
            return
        last_channel_alert[kind] = now
        failure_streak[kind] = 0
        try:
            await bot.send_message(
                settings.channel_id,
                "WARN технические работы: часть прогнозов задерживается. Оплаченное не пропадёт.",
            )
        except Exception:  # noqa: BLE001
            log.error("не удалось предупредить канал о сбое")

    async def send_due_forecasts() -> None:
        """Разослать прогнозы админам за N часов до матча.

        Пока продаж нет, получатели — только админы. Права и идемпотентность
        обеспечивает журнал deliveries: второй раз тот же матч не уйдёт.
        """
        if pool is None:
            return
        try:
            # Получатели: админы + все с действующей подпиской или пробным
            # доступом. Список берём из базы каждый раз — новый подписчик
            # начинает получать прогнозы со следующего прохода.
            recipients = set(settings.admin_ids)
            try:
                for row in await db.active_subscribers(pool):
                    recipients.add(int(row["chat_id"]))
            except Exception as exc:  # noqa: BLE001
                log.error("не удалось получить подписчиков: %s", exc)
            log.info(
                "получателей прогнозов: %d (админов %d)",
                len(recipients),
                len(settings.admin_ids),
            )
            report = await deliver_due(
                pool,
                lead_hours=settings.forecast_lead_hours,
                dry_run=False,
                send=deliver_to_chat,
                recipients=sorted(recipients),
            )
            if report.sent:
                log.info("прогнозов отправлено: %d", report.sent)
            failure_streak["delivery"] = 0
            if report.failed:
                log.warning("не отправлено: %d", report.failed)
            # Алерт админу: молчаливый сбой рассылки — худший вид сбоя.
            if report.failed > 0:
                for admin_id in settings.admin_ids:
                    try:
                        await bot.send_message(
                            admin_id,
                            "WARN сбой рассылки: не отправлено %d из %d"
                            % (report.failed, report.due),
                        )
                    except Exception:  # noqa: BLE001
                        continue
        except Exception as exc:  # noqa: BLE001
            log.error("рассылка не удалась: %s", exc)
            failure_streak["delivery"] += 1
            await notify_channel_failure("delivery", exc)
            for admin_id in settings.admin_ids:
                try:
                    await bot.send_message(admin_id, "Рассылка упала: %s" % exc)
                except Exception:  # noqa: BLE001
                    continue

    scheduler.add_job(send_due_forecasts, "interval", minutes=10, id="deliver")

    db_failures = 0

    async def heartbeat_tick() -> None:
        nonlocal db_failures
        if pool is None:
            return
        try:
            await db.beat(pool)
            db_failures = 0
        except Exception as exc:  # noqa: BLE001
            db_failures += 1
            log.error("не удалось записать пульс (%d раз подряд): %s", db_failures, exc)
            if db_failures >= 3:
                # База недоступна три цикла подряд. Тихо жить без неё нельзя:
                # выходим, чтобы супервизор поднял процесс и туннель заново.
                log.critical("база недоступна 30 минут — перезапускаюсь")
                os._exit(1)

    scheduler.add_job(heartbeat_tick, "interval", minutes=10, id="heartbeat")

    async def watch_health() -> None:
        """Если пульса нет 30 минут — сообщить админу. Тишина не должна быть нормой."""
        if pool is None or not settings.admin_ids:
            return
        try:
            age = await db.heartbeat_age(pool)
        except Exception as exc:  # noqa: BLE001
            log.error("проверка пульса не удалась: %s", exc)
            return
        if age is None:
            return
        if age > 1800:
            for admin_id in settings.admin_ids:
                try:
                    await bot.send_message(
                        admin_id,
                        "ALERT бот не подаёт признаков жизни: пульс %.0f минут назад." % (age / 60),
                    )
                except Exception:  # noqa: BLE001
                    continue

    scheduler.add_job(watch_health, "interval", minutes=30, id="watch_health")

    async def remind_expiring() -> None:
        """Напомнить о продлении за сутки до окончания подписки."""
        if pool is None:
            return
        try:
            chat_ids = await db.expiring_subscriptions(pool, 24)
        except Exception as exc:  # noqa: BLE001
            log.error("не удалось получить истекающие подписки: %s", exc)
            return
        for chat_id in chat_ids:
            try:
                await bot.send_message(
                    chat_id,
                    "Подписка заканчивается в ближайшие сутки. "
                    "Продлить: /tariffs. Автопродления нет — деньги сами не спишутся.",
                )
                await db.log_event(pool, chat_id, "renewal_reminder")
            except Exception:  # noqa: BLE001
                continue

    scheduler.add_job(
        remind_expiring,
        "cron",
        hour=12,
        minute=0,
        timezone="Europe/Moscow",
        id="renewal_reminder",
    )

    async def publish_accuracy() -> None:
        """Раз в неделю — пост с публичной точностью в канал."""
        if pool is None or not settings.channel_id:
            return
        try:
            from football_analyzer.cli import _accuracy

            report = await _accuracy(settings.require_database_url(), 30)
            await bot.send_message(settings.channel_id, accuracy_post(report))
            log.info("опубликован пост точности")
        except Exception as exc:  # noqa: BLE001
            log.error("пост точности не удался: %s", exc)

    async def weekly_digest() -> None:
        """Раз в неделю — сводка подписчикам: что будет и напоминание о боте."""
        if pool is None:
            return
        try:
            await send_digest(bot, pool)
        except Exception as exc:  # noqa: BLE001
            log.error("дайджест не отправлен: %s", exc)

    scheduler.add_job(
        weekly_digest,
        "cron",
        day_of_week="mon",
        hour=10,
        minute=0,
        timezone="Europe/Moscow",
        id="weekly_digest",
    )

    scheduler.add_job(
        publish_accuracy,
        "cron",
        day_of_week="mon",
        hour=9,
        minute=0,
        timezone="Europe/Moscow",
        id="accuracy_weekly",
    )

    async def publish_to_channel() -> None:
        """Пост в канал: 10:00 и 18:00 МСК."""
        if pool is None or not settings.channel_id:
            return
        try:
            await publish_next(bot, pool, settings.channel_id)
        except Exception as exc:  # noqa: BLE001
            log.error("публикация в канал не удалась: %s", exc)
            failure_streak["publish"] += 1
            await notify_channel_failure("publish", exc)
            for admin_id in settings.admin_ids:
                try:
                    await bot.send_message(admin_id, "Пост в канал не ушёл: %s" % exc)
                except Exception:  # noqa: BLE001
                    continue

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
