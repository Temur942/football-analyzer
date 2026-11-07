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

from football_analyzer.analysis.forecast import build_forecast, render_text
from football_analyzer.analysis.service import CUP_PREFIXES, fit_all_models
from football_analyzer.bot.access import BETA_TEXT, CONSENT_TEXT
from football_analyzer.bot.docs import DOCUMENTS, delete_messages, send_document
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
    builder.button(text="📄 Документы", callback_data="docs_menu")
    builder.adjust(1)
    return builder.as_markup()


def consent_keyboard() -> InlineKeyboardMarkup:
    """Кнопки экрана согласия. Документы доступны ДО согласия."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Согласен, продолжить", callback_data="consent_yes")
    builder.button(text="📄 Публичная оферта", callback_data="doc_offer")
    builder.button(text="📄 Политика ПД", callback_data="doc_privacy")
    builder.button(text="❌ Не согласен", callback_data="consent_no")
    builder.adjust(1)
    return builder.as_markup()


def docs_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, (title, _) in DOCUMENTS.items():
        builder.button(text=title, callback_data="doc_" + key)
    builder.button(text="⬅️ В меню", callback_data=CALLBACK_BACK)
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
async def handle_start(message: Message, has_access: bool = True, has_consent: bool = True) -> None:
    if not has_consent:
        await message.answer(CONSENT_TEXT, reply_markup=consent_keyboard())
        return
    if not has_access:
        # Закрытая бета: показываем свой ID, чтобы владелец мог добавить
        # человека в админы вручную.
        user_id = message.from_user.id if message.from_user else 0
        await message.answer(BETA_TEXT % user_id)
        return
    await message.answer(start_text(), reply_markup=main_menu())


@router.callback_query(F.data == "consent_yes")
async def handle_consent_yes(callback: CallbackQuery, pool: asyncpg.Pool | None = None) -> None:
    user = callback.from_user
    if pool is not None:
        try:
            await db.record_consent(pool, user.id, user.username)
        except Exception as exc:  # noqa: BLE001
            log.error("не удалось записать согласие: %s", exc)
    if callback.message:
        await callback.message.answer(start_text(), reply_markup=main_menu())
    await callback.answer("Согласие сохранено")


@router.callback_query(F.data == "consent_no")
async def handle_consent_no(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(
            "Без согласия на обработку данных Сервис работать не может. "
            "Если передумаете — нажмите /start."
        )
    await callback.answer()


# id сообщений с текстом документа: нужны, чтобы «Закрыть» убирал их из чата.
_DOC_MESSAGES: dict[int, list[int]] = {}


def doc_nav() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ К документам", callback_data="docs_list")
    builder.button(text="✖️ Закрыть", callback_data="docs_close")
    builder.adjust(2)
    return builder.as_markup()


@router.callback_query(F.data.in_({"docs_menu", "docs_list"}))
async def handle_docs_menu(callback: CallbackQuery) -> None:
    chat_id = callback.from_user.id
    stale = _DOC_MESSAGES.pop(chat_id, [])
    if stale:
        await delete_messages(callback.bot, chat_id, stale)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "📄 <b>Документы</b>\n\nВыберите, что открыть:", reply_markup=docs_menu()
        )
    await callback.answer()


@router.callback_query(F.data == "docs_close")
async def handle_docs_close(callback: CallbackQuery) -> None:
    chat_id = callback.from_user.id
    stale = _DOC_MESSAGES.pop(chat_id, [])
    if stale:
        await delete_messages(callback.bot, chat_id, stale)
    if isinstance(callback.message, Message):
        await callback.message.answer(start_text(), reply_markup=main_menu())
    await callback.answer("Закрыто")


@router.callback_query(F.data.startswith("doc_"))
async def handle_document(callback: CallbackQuery) -> None:
    key = (callback.data or "").removeprefix("doc_")
    if not isinstance(callback.message, Message) or key not in DOCUMENTS:
        await callback.answer()
        return
    chat_id = callback.from_user.id
    stale = _DOC_MESSAGES.pop(chat_id, [])
    if stale:
        await delete_messages(callback.bot, chat_id, stale)
    try:
        ids = await send_document(callback.message, key)
    except FileNotFoundError:
        await callback.message.answer("Документ временно недоступен.")
        await callback.answer()
        return
    _DOC_MESSAGES[chat_id] = ids
    await callback.message.answer("Это весь документ. Что дальше?", reply_markup=doc_nav())
    await callback.answer()


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
    """Посчитать прогнозы на ближайшие матчи и показать их прямо в чате."""
    if callback.message is None:
        await callback.answer()
        return
    if pool is None:
        await callback.message.answer(
            "База сейчас недоступна, поэтому прогнозы посчитать нельзя. "
            "Попробуйте через пару минут.",
            reply_markup=main_menu(),
        )
        await callback.answer("Нет доступа к базе")
        return

    await callback.message.answer("⏳ Считаю прогнозы…")
    await callback.answer()

    rows = await db.upcoming_matches(pool, 3)
    if not rows:
        await callback.message.answer(
            "На ближайшие дни матчей нет. Загляните позже.", reply_markup=main_menu()
        )
        return

    model = await fit_all_models(pool)
    sent = 0
    for row in rows:
        competition_id = row["competition_id"]
        rates: object | None
        if competition_id.startswith(CUP_PREFIXES):
            rates = model
        else:
            rates = model.league_rates.get(competition_id)
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
        await callback.message.answer(
            render_text(forecast, row["name_ru"]), reply_markup=main_menu()
        )
        sent += 1
        if sent >= 3:
            break

    if sent == 0:
        await callback.message.answer(
            "По ближайшим матчам пока не хватает истории для расчёта.",
            reply_markup=main_menu(),
        )
