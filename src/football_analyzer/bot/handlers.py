"""Хендлеры бота: меню, тариф, пример прогноза, подписка.

Тексты и клавиатуры вынесены в чистые функции, чтобы их можно было проверить
без подключения к Telegram: сеть для этого не нужна.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import asyncpg
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from football_analyzer.bot.access import BETA_TEXT
from football_analyzer.config import get_settings
from football_analyzer.storage import db

log = logging.getLogger(__name__)
router = Router(name="main")

CALLBACK_TARIFFS = "tariffs"
CALLBACK_BUY = "buy"
CALLBACK_PAY = "pay"
CALLBACK_SUBSCRIPTION = "subscription"
CALLBACK_BACK = "back"


def main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Прогноз на сегодня", callback_data="today")
    builder.button(text="💳 Тарифы и подписка", callback_data=CALLBACK_TARIFFS)
    builder.button(text="ℹ️ Как это работает", callback_data="about")
    builder.adjust(1)
    return builder.as_markup()


def tariffs_menu() -> InlineKeyboardMarkup:
    settings = get_settings()
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Оформить за %d ₽/мес" % settings.subscription_price_rub,
        callback_data=CALLBACK_BUY,
    )
    builder.button(text="⬅️ В меню", callback_data=CALLBACK_BACK)
    builder.adjust(1)
    return builder.as_markup()


def start_text() -> str:
    settings = get_settings()
    return (
        "⚽ <b>Football Analyzer</b>\n\n"
        "Математический анализ футбольных матчей: вероятности голов, угловых "
        "и карточек по 20 соревнованиям.\n\n"
        "Это не советы для ставок, а расчёт вероятностей по истории матчей.\n\n"
        "Подписка — <b>%d ₽ в месяц</b>.\n\n"
        "Выберите действие:" % settings.subscription_price_rub
    )


def tariffs_text() -> str:
    settings = get_settings()
    return (
        "💳 <b>Подписка</b>\n\n"
        "• Прогнозы за 5 часов до каждого матча\n"
        "• 20 соревнований: топ-5 лиг, вторые дивизионы, еврокубки\n"
        "• Тоталы, исходы, счёт, индивидуальные тоталы, углы, ЖК — в процентах\n"
        "• Отмена в любой момент\n\n"
        "<b>Цена: %d ₽ за %d дней</b>\n\n"
        "Оплата: ЮKassa (карты РФ)." % (settings.subscription_price_rub, settings.subscription_days)
    )


def about_text() -> str:
    return (
        "ℹ️ <b>Как это работает</b>\n\n"
        "1. Бот круглосуточно следит за расписанием матчей.\n"
        "2. По истории матчей считает силы команд и вероятности событий.\n"
        "3. За 5 часов до игры присылает прогноз в процентах.\n\n"
        "Модель считает вероятности, а не гарантирует результат: "
        "любой матч может закончиться иначе.\n\n"
        "18+. Это аналитика, не призыв к ставкам."
    )


def order_text() -> str:
    """Экран заказа: товар, срок, цена и кнопка оплаты."""
    settings = get_settings()
    return (
        "🧾 <b>Оформление заказа</b>\n\n"
        "Товар: подписка «Прогнозы на футбол»\n"
        "Срок: <b>%d дней</b>\n"
        "Цена: <b>%d ₽</b>\n\n"
        "Оплата: ЮKassa — карты РФ, СБП.\n"
        "Доступ откроется сразу после оплаты, продление — по желанию.\n\n"
        "18+. Аналитика, не призыв к ставкам."
        % (settings.subscription_days, settings.subscription_price_rub)
    )


def order_menu() -> InlineKeyboardMarkup:
    settings = get_settings()
    builder = InlineKeyboardBuilder()
    builder.button(
        text="💳 Оплатить %d ₽" % settings.subscription_price_rub,
        callback_data=CALLBACK_PAY,
    )
    builder.button(text="⬅️ К тарифам", callback_data=CALLBACK_TARIFFS)
    builder.adjust(1)
    return builder.as_markup()


def subscription_text(paid_until: datetime | None) -> str:
    if paid_until is None or paid_until <= datetime.now(tz=UTC):
        return (
            "🔓 <b>Подписка не активна</b>\n\n"
            "Оформите подписку, чтобы получать прогнозы за 5 часов до матча."
        )
    return "✅ <b>Подписка активна</b> до %s" % paid_until.strftime("%d.%m.%Y")


@router.message(CommandStart())
async def handle_start(message: Message, has_access: bool = True) -> None:
    if not has_access:
        # Закрытая бета: показываем свой ID, чтобы владелец мог добавить
        # человека в админы вручную.
        user_id = message.from_user.id if message.from_user else 0
        await message.answer(BETA_TEXT % user_id)
        return
    await message.answer(start_text(), reply_markup=main_menu())


@router.message(Command("myid"))
async def handle_myid(message: Message) -> None:
    """Показать свой Telegram ID — нужен, чтобы выдать доступ."""
    user = message.from_user
    if user is None:
        return
    await message.answer(
        "Ваш ID: <code>%d</code>\nUsername: %s"
        % (user.id, "@" + user.username if user.username else "—")
    )


@router.message(Command("help"))
async def handle_help(message: Message, has_access: bool = True) -> None:
    await message.answer(about_text(), reply_markup=main_menu() if has_access else None)


@router.message(Command("tariffs"))
async def handle_tariffs(message: Message) -> None:
    await message.answer(tariffs_text(), reply_markup=tariffs_menu())


@router.callback_query(F.data == CALLBACK_TARIFFS)
async def handle_tariffs_button(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(tariffs_text(), reply_markup=tariffs_menu())
    await callback.answer()


@router.callback_query(F.data == CALLBACK_BUY)
async def handle_buy(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(order_text(), reply_markup=order_menu())
    await callback.answer()


@router.callback_query(F.data == CALLBACK_PAY)
async def handle_pay(callback: CallbackQuery) -> None:
    # Здесь будет вызов ЮKassa: создание платежа и ссылка на оплату.
    if callback.message:
        await callback.message.answer(
            "Оплата ещё не включена: заявка в ЮKassa на проверке.\n"
            "Как только платёж заработает, эта кнопка откроет страницу оплаты.",
            reply_markup=order_menu(),
        )
    await callback.answer("Оплата подключается")


@router.callback_query(F.data == "about")
async def handle_about(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(about_text(), reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == CALLBACK_BACK)
async def handle_back(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(start_text(), reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == "today")
async def handle_today(callback: CallbackQuery, pool: asyncpg.Pool | None = None) -> None:
    """Ближайший матч с уже посчитанным прогнозом.

    Пул приходит от Dispatcher(pool=...) — aiogram подставляет его по имени
    параметра. Если базы нет, бот всё равно отвечает.
    """
    text = "Пока нет рассчитанных прогнозов. Они появятся перед матчами."
    if pool is not None:
        row = await db.latest_prediction(pool)
        if row is not None:
            text = "📊 <b>Свежий прогноз</b>\n\n" + row
    if callback.message:
        await callback.message.answer(text, reply_markup=main_menu())
    await callback.answer()
