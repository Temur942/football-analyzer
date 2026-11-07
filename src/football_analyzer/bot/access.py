"""Контроль доступа: закрытая бета для всех, кроме админов и подписчиков.

Зачем: пока оплата не подключена, бот не должен стать бесплатным для всех.
При этом владелец пользуется им свободно — его Telegram ID в списке админов.

Правило простое и проверяемое:
    доступ = (id в BOT_ADMIN_IDS) ИЛИ (есть действующая подписка в базе)

Команды /start, /help и /myid доступны всем — иначе человек не поймёт, что
происходит, и мы не узнаем его ID для добавления в админы.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from football_analyzer.storage import db

log = logging.getLogger(__name__)

ALLOWED_COMMANDS = ("/start", "/help", "/myid", "/docs", "/start@", "/help@", "/myid@", "/docs@")
# Эти кнопки нажимаются до согласия — иначе человек не сможет его дать.
CONSENT_CALLBACKS = (
    "consent_yes",
    "consent_no",
    "doc_offer",
    "doc_terms",
    "doc_privacy",
    "docs_list",
    "docs_close",
)

CONSENT_TEXT = (
    "👋 <b>Перед началом работы</b>\n\n"
    "Сервис предназначен для лиц <b>18 лет и старше</b>.\n\n"
    "Я предоставляю <b>математический анализ</b> матчей — вероятности событий, "
    "а не гарантии результата, не советы по ставкам и не призыв к ним. "
    "Решения вы принимаете самостоятельно.\n\n"
    "Для работы нужны ваши данные: идентификатор Telegram, имя пользователя, "
    "сведения о подписке и платежах. Данные банковской карты мне не передаются — "
    "их обрабатывает ЮKassa.\n\n"
    "Данные хранятся на серверах <b>в Российской Федерации</b>. Для доставки "
    "сообщений идентификатор Telegram и текст сообщения передаются сервису "
    "<b>Telegram</b> за пределами РФ — это трансграничная передача по ст. 12 "
    "152-ФЗ, и она выполняется с вашего согласия.\n\n"
    "Нажимая «Согласен», вы подтверждаете возраст 18+, согласие на обработку "
    "персональных данных и на трансграничную передачу."
)

BETA_TEXT = (
    "🔒 <b>Закрытая бета</b>\n\n"
    "Сервис готовится к запуску: подписка ещё не подключена.\n\n"
    "Как только оплата заработает, здесь появится кнопка оформления.\n\n"
    "Ваш ID: <code>%s</code> — отправьте его администратору, "
    "если хотите получить доступ раньше."
)


def _is_allowed_command(event: object) -> bool:
    text = getattr(event, "text", None)
    if not text:
        return False
    command = text.split()[0].lower()
    return any(command.startswith(prefix) for prefix in ALLOWED_COMMANDS)


class AccessMiddleware(BaseMiddleware):
    """Пускать админов и подписчиков; остальным — сообщение о закрытой бете."""

    def __init__(self, pool: asyncpg.Pool | None) -> None:
        self.pool = pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        admin_ids = _admin_ids()
        has_access = user.id in admin_ids

        if not has_access and self.pool is not None:
            has_access = await db.has_active_subscription(self.pool, user.id)

        # Согласие по 152-ФЗ нужно ДО любой обработки и до оплаты.
        has_consent = False
        if self.pool is not None:
            try:
                has_consent = await db.has_consent(self.pool, user.id)
            except Exception as exc:  # noqa: BLE001
                log.error("не удалось проверить согласие: %s", exc)
        data["has_consent"] = has_consent
        data["has_access"] = has_access
        data["is_admin"] = user.id in admin_ids

        if not has_consent:
            kind = (
                "callback:" + str(getattr(event, "data", ""))
                if isinstance(event, CallbackQuery)
                else "message:" + str(getattr(event, "text", ""))
            )
            log.info("нет согласия: id=%s | %s", user.id, kind[:60])
            if isinstance(event, CallbackQuery):
                if event.data in CONSENT_CALLBACKS:
                    return await handler(event, data)
                await event.answer(
                    "Сначала подтвердите согласие на обработку данных", show_alert=True
                )
                return None
            if isinstance(event, Message) and _is_allowed_command(event):
                return await handler(event, data)
            if isinstance(event, Message):
                await event.answer(
                    "Чтобы пользоваться Сервисом, нужно согласие на обработку "
                    "персональных данных. Нажмите /start."
                )
            return None

        # Диагностика: видно, что именно пришло и что мы решили. Без этого
        # «бот молчит» не отличить от «бот не получил сообщение».
        kind = (
            "callback:" + str(getattr(event, "data", ""))
            if isinstance(event, CallbackQuery)
            else "message:" + str(getattr(event, "text", ""))
        )
        log.info(
            "update от id=%s (%s) | %s | доступ=%s админ=%s",
            user.id,
            user.username,
            kind[:60],
            has_access,
            user.id in admin_ids,
        )

        if has_access:
            return await handler(event, data)

        # Не админ: логируем ID — по нему владелец решает, кого пускать.
        log.warning("закрытая бета: обращение от id=%s username=%s", user.id, user.username)

        if isinstance(event, Message):
            if _is_allowed_command(event):
                return await handler(event, data)
            await event.answer(BETA_TEXT % user.id)
            return None

        if isinstance(event, CallbackQuery):
            await event.answer("Закрытая бета: подписка ещё не подключена", show_alert=True)
            return None

        return None


def _admin_ids() -> set[int]:
    from football_analyzer.config import get_settings

    return set(get_settings().admin_ids)
