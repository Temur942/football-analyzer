"""Хендлеры бота: меню, тариф, пример прогноза, подписка.

Тексты и клавиатуры вынесены в чистые функции, чтобы их можно было проверить
без подключения к Telegram: сеть для этого не нужна.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import aiohttp
import asyncpg
from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from football_analyzer.analysis.forecast import build_forecast, render_payload, render_text
from football_analyzer.analysis.service import CUP_PREFIXES, fit_all_models
from football_analyzer.bot.access import BETA_TEXT, CONSENT_TEXT
from football_analyzer.bot.docs import DOCUMENTS, delete_messages, send_document
from football_analyzer.config import get_settings
from football_analyzer.payments.yookassa import create_payment
from football_analyzer.storage import db

log = logging.getLogger(__name__)
router = Router(name="main")

CALLBACK_TARIFFS = "tariffs"
# Тарифы: срок в днях, цена в рублях. Вход 149 ₽ снимает страх первой
# покупки — у конкурента (FlashStats) 7 дней стоят 99 ₽, 30 дней 299 ₽.
TARIFFS: tuple[tuple[int, int], ...] = ((7, 149), (30, 499), (90, 1199))
TRIAL_DAYS = 3

CALLBACK_BUY = "buy"
CALLBACK_PAY = "pay"
CALLBACK_SUBSCRIPTION = "subscription"
CALLBACK_BACK = "back"


def main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Прогноз на сегодня", callback_data="today")
    builder.button(text="💳 Тарифы и подписка", callback_data=CALLBACK_TARIFFS)
    builder.button(text="ℹ️ Как это работает", callback_data="about")
    builder.button(text="📜 Мои прогнозы", callback_data="my_predictions")
    builder.button(text="⭐ Моя подписка", callback_data="my_subscription")
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
    builder = InlineKeyboardBuilder()
    for days, price in TARIFFS:
        builder.button(text="%d дней — %d ₽" % (days, price), callback_data="buy:%d" % days)
    builder.button(text="⬅️ В меню", callback_data=CALLBACK_BACK)
    builder.adjust(1)
    return builder.as_markup()


def start_text() -> str:
    return (
        "⚽ <b>Football Analyzer</b>\n\n"
        "Математический анализ футбольных матчей: вероятности голов, угловых "
        "и карточек по 20 соревнованиям.\n\n"
        "Это не советы для ставок, а расчёт вероятностей по истории матчей.\n\n"
        "Подписка — от <b>%d ₽</b>, первые %d дня бесплатно.\n\n"
        "Выберите действие:" % (TARIFFS[0][1], TRIAL_DAYS)
    )


def tariffs_text() -> str:
    lines = [
        "💳 <b>Подписка</b>",
        "",
        "• Прогнозы за 5 часов до каждого матча",
        "• 20 соревнований: топ-5 лиг, вторые дивизионы, еврокубки",
        "• Тоталы, исходы, счёт, индивидуальные тоталы, углы, ЖК — в процентах",
        "• Автопродления нет: срок кончился — ничего не списывается",
        "",
        "<b>Тарифы</b>",
    ]
    for days, price in TARIFFS:
        lines.append("▫️ %d дней — <b>%d ₽</b> (%.0f ₽/день)" % (days, price, price / days))
    lines += [
        "",
        "Первые %d дня — <b>бесплатно</b>, чтобы проверить." % TRIAL_DAYS,
        "Оплата: ЮKassa (карты РФ, СБП).",
    ]
    return "\n".join(lines)


def onboarding_text() -> str:
    """Первое сообщение после согласия: что нажимать и как выглядит прогноз."""
    return (
        "🎓 <b>Как пользоваться</b>\n\n"
        "1. Нажмите «📊 Прогноз на сегодня» — бот посчитает ближайшие матчи.\n"
        "2. Дальше прогнозы будут приходить сами, за 5 часов до матча.\n"
        "3. «⭐ Моя подписка» — срок и тарифы, /faq — частые вопросы.\n\n"
        "Вот как выглядит прогноз:\n\n"
        "⚽ <b>Команда А — Команда Б</b>\n"
        "🕐 сегодня, 20:30 МСК\n\n"
        "🎯 Исход: П1 52% · Х 24% · П2 23%\n"
        "🥅 Тотал: ТБ 1,5 — 79% · ТБ 2,5 — 56% · ТБ 3,5 — 33%\n"
        "🔮 Счёт: 1:1 11% · 2:1 10% · 1:0 9%\n\n"
        "Это вероятности, а не гарантии. 18+."
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


def tariff(days: int) -> tuple[int, int]:
    """Найти тариф по сроку. Неизвестный срок — основной тариф."""
    for known_days, price in TARIFFS:
        if known_days == days:
            return known_days, price
    return TARIFFS[1]


def order_text(days: int = 30, price: int = 499) -> str:
    """Экран заказа: товар, срок, цена и кнопка оплаты."""
    return (
        "🧾 <b>Оформление заказа</b>\n\n"
        "Товар: подписка «Прогнозы на футбол»\n"
        "Срок: <b>%d дней</b>\n"
        "Цена: <b>%d ₽</b>\n\n"
        "Оплата: ЮKassa — карты РФ, СБП.\n"
        "Доступ откроется сразу после оплаты, автопродления нет.\n\n"
        "18+. Аналитика, не призыв к ставкам." % (days, price)
    )


def order_menu(days: int = 30, price: int = 499) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Оплатить %d ₽" % price, callback_data="pay:%d" % days)
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


async def _event(pool: asyncpg.Pool | None, chat_id: int, name: str) -> None:
    """Шаг воронки. Сбой записи не должен мешать основному сценарию."""
    if pool is None:
        return
    try:
        await db.log_event(pool, chat_id, name)
    except Exception as exc:  # noqa: BLE001
        log.error("не удалось записать событие %s: %s", name, exc)


def _source_from(payload: str | None) -> str | None:
    """Источник привлечения из ?start=ch_<код>."""
    if not payload or not payload.startswith("ch_"):
        return None
    return payload.removeprefix("ch_")[:32]


def _match_from(payload: str | None) -> int | None:
    """id матча из deep-link ?start=match_<id>."""
    if not payload or not payload.startswith("match_"):
        return None
    try:
        return int(payload.removeprefix("match_"))
    except ValueError:
        return None


def _referrer_from(payload: str | None) -> int | None:
    """Достать id пригласившего из ?start=ref_<id>."""
    if not payload or not payload.startswith("ref_"):
        return None
    try:
        return int(payload.removeprefix("ref_"))
    except ValueError:
        return None


@router.message(CommandStart())
async def handle_start(
    message: Message,
    command: CommandObject | None = None,
    pool: asyncpg.Pool | None = None,
    has_access: bool = True,
    has_consent: bool = True,
) -> None:
    user = message.from_user
    if user is None:
        return
    # Реферальная ссылка: ?start=ref_<id>
    referrer = _referrer_from(command.args if command else None)
    if referrer is not None and pool is not None:
        try:
            if await db.set_referrer(pool, user.id, referrer):
                await message.answer(
                    "🎁 Вас пригласил друг: когда оформите подписку, он получит бонусные дни."
                )
        except Exception as exc:  # noqa: BLE001
            log.error("не удалось записать пригласившего: %s", exc)
    source = _source_from(command.args if command else None)
    if source is not None and pool is not None:
        try:
            await db.set_source(pool, user.id, source)
        except Exception as exc:  # noqa: BLE001
            log.error("не удалось записать источник: %s", exc)
    await _event(pool, user.id, "start")

    if not has_consent:
        await message.answer(CONSENT_TEXT, reply_markup=consent_keyboard())
        return
    if not has_access:
        # Закрытая бета: показываем свой ID, чтобы владелец мог добавить
        # человека в админы вручную.
        user_id = message.from_user.id if message.from_user else 0
        await message.answer(BETA_TEXT % user_id)
        return
    match_id = _match_from(command.args if command else None)
    if match_id is not None and pool is not None:
        if await send_match_forecast(message, pool, match_id):
            return
    await message.answer(start_text(), reply_markup=main_menu())


@router.callback_query(F.data == "consent_yes")
async def handle_consent_yes(callback: CallbackQuery, pool: asyncpg.Pool | None = None) -> None:
    user = callback.from_user
    await _event(pool, user.id, "consent")
    trial_until = None
    if pool is not None:
        try:
            await db.record_consent(pool, user.id, user.username)
            trial_until = await db.start_trial(pool, user.id, TRIAL_DAYS)
        except Exception as exc:  # noqa: BLE001
            log.error("не удалось записать согласие: %s", exc)
    if callback.message:
        if trial_until is not None:
            await callback.message.answer(
                "🎁 <b>Пробный доступ на %d дня</b> — до %s.\n"
                "Проверьте прогнозы и решите, нужна ли подписка."
                % (TRIAL_DAYS, trial_until.strftime("%d.%m.%Y"))
            )
        await callback.message.answer(onboarding_text(), reply_markup=main_menu())
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


@router.channel_post()
async def log_channel_post(message: Message) -> None:
    """Любое сообщение в канале: печатаем chat_id — он нужен для публикаций."""
    log.warning(
        "CHANNEL_ID=%s | канал: %s | текст: %s",
        message.chat.id,
        message.chat.title,
        (message.text or "")[:40],
    )


@router.my_chat_member()
async def log_chat_member(event: ChatMemberUpdated) -> None:
    """Бота добавили в канал — тоже фиксируем id."""
    log.warning(
        "CHANNEL_ID=%s | канал: %s | статус: %s",
        event.chat.id,
        event.chat.title,
        event.new_chat_member.status,
    )


@router.message(Command("stats"))
async def handle_stats(
    message: Message,
    pool: asyncpg.Pool | None = None,
    is_admin: bool = False,
) -> None:
    """Сводка для администратора. Остальным — отказ, без подробностей."""
    if not is_admin:
        await message.answer("Команда доступна только администратору.")
        return
    if pool is None:
        await message.answer("База недоступна, сводку не собрать.")
        return

    summary = await db.admin_summary(pool)
    users = await db.recent_subscribers(pool, 5)

    lines = [
        "📊 <b>Сводка</b>",
        "",
        "👤 Пользователей: <b>%d</b> (с согласием: %d)" % (summary["users"], summary["consented"]),
        "⭐ Активных подписок: <b>%d</b>" % summary["active_subs"],
        "",
        "⚽ Матчей в базе: <b>%d</b>, впереди: %d" % (summary["matches"], summary["upcoming"]),
        "🔮 Прогнозов: <b>%d</b>" % summary["predictions"],
        "📨 Отправок: %d (пробных %d, ошибок %d)"
        % (summary["sent"], summary["dry_run"], summary["failed"]),
    ]
    if users:
        lines += ["", "👥 <b>Последние пользователи</b>"]
        for row in users:
            paid = row["paid_until"].strftime("%d.%m.%Y") if row["paid_until"] else "нет"
            name = ("@" + row["username"]) if row["username"] else str(row["chat_id"])
            lines.append("▫️ %s — подписка до %s" % (name, paid))
    await message.answer("\n".join(lines))


@router.callback_query(F.data == "my_subscription")
async def handle_my_subscription(callback: CallbackQuery, pool: asyncpg.Pool | None = None) -> None:
    """Статус подписки: до какого числа действует или что её нет."""
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    paid_until = None
    if pool is not None:
        try:
            paid_until = await db.subscription_until(pool, callback.from_user.id)
        except Exception as exc:  # noqa: BLE001
            log.error("не удалось получить подписку: %s", exc)
    enabled = True
    if pool is not None:
        try:
            enabled = await db.notifications_state(pool, callback.from_user.id)
        except Exception as exc:  # noqa: BLE001
            log.error("не удалось узнать состояние уведомлений: %s", exc)
    builder = InlineKeyboardBuilder()
    builder.button(
        text=("🔕 Выключить рассылку" if enabled else "🔔 Включить рассылку"),
        callback_data="toggle_notifications",
    )
    builder.button(text="💳 Тарифы", callback_data=CALLBACK_TARIFFS)
    builder.button(text="⬅️ В меню", callback_data=CALLBACK_BACK)
    builder.adjust(1)
    await callback.message.answer(subscription_text(paid_until), reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == "toggle_notifications")
async def handle_toggle_notifications(
    callback: CallbackQuery, pool: asyncpg.Pool | None = None
) -> None:
    """Выключить или включить автоматическую рассылку. Подписка не страдает."""
    if pool is None:
        await callback.answer("База недоступна", show_alert=True)
        return
    enabled = await db.notifications_state(pool, callback.from_user.id)
    await db.set_notifications(pool, callback.from_user.id, not enabled)
    await _event(
        pool, callback.from_user.id, "notifications_on" if not enabled else "notifications_off"
    )
    await callback.answer("Рассылка включена" if not enabled else "Рассылка выключена")
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "🔔 Прогнозы снова будут приходить автоматически."
            if not enabled
            else "🔕 Автоматические прогнозы выключены. Доступ и подписка остались — "
            "прогноз можно смотреть кнопкой «📊 Прогноз на сегодня».",
            reply_markup=main_menu(),
        )


@router.callback_query(F.data == "my_predictions")
async def handle_my_predictions(callback: CallbackQuery, pool: asyncpg.Pool | None = None) -> None:
    if pool is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    rows = await db.user_predictions(pool, callback.from_user.id, 5)
    if not rows:
        await callback.message.answer(
            "📜 <b>Мои прогнозы</b>\n\nПока пусто: я ещё не отправлял вам прогнозы. "
            "Они приходят за 5 часов до матча.",
            reply_markup=main_menu(),
        )
        await callback.answer()
        return
    builder = InlineKeyboardBuilder()
    for row in rows:
        day = row["kickoff_utc"].strftime("%d.%m")
        builder.button(
            text="%s %s — %s" % (day, row["home_team"], row["away_team"]),
            callback_data="pred:%d" % row["id"],
        )
    builder.button(text="⬅️ В меню", callback_data=CALLBACK_BACK)
    builder.adjust(1)
    await callback.message.answer(
        "📜 <b>Мои прогнозы</b>\n\nПоследние отправленные. Выберите матч:",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pred:"))
async def handle_prediction_detail(
    callback: CallbackQuery, pool: asyncpg.Pool | None = None
) -> None:
    """Показать сохранённый прогноз ровно в том виде, в каком он ушёл."""
    if pool is None or not isinstance(callback.message, Message):
        await callback.answer()
        return
    match_id = int((callback.data or "pred:0").split(":")[1])
    payload = await db.prediction_payload(pool, match_id)
    if not payload:
        await callback.message.answer("Прогноз для этого матча не сохранился.")
        await callback.answer()
        return
    data = json.loads(payload)
    await callback.message.answer(render_payload(data))
    await callback.answer()


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


async def send_match_forecast(message: Message, pool: asyncpg.Pool, match_id: int) -> bool:
    """Показать прогноз конкретного матча (deep-link). False — если не нашли."""
    row = await db.match_for_forecast(pool, match_id)
    if row is None:
        return False
    payload = await db.prediction_payload(pool, match_id)
    if payload:
        await message.answer(render_payload(json.loads(payload), row["name_ru"]))
        return True
    model = await fit_all_models(pool)
    rates = (
        model
        if row["competition_id"].startswith(CUP_PREFIXES)
        else model.league_rates.get(row["competition_id"])
    )
    if rates is None:
        return False
    forecast = build_forecast(
        rates,  # type: ignore[arg-type]
        competition_id=row["competition_id"],
        home_team=row["home_team"],
        away_team=row["away_team"],
        kickoff_utc=row["kickoff_utc"],
    )
    await message.answer(render_text(forecast, row["name_ru"]))
    return True


@router.message(Command("faq"))
async def handle_faq(message: Message, has_access: bool = True) -> None:
    await message.answer(
        "❓ <b>Частые вопросы</b>\n\n"
        "<b>Это прогнозы для ставок?</b>\n"
        "Нет. Сервис не принимает ставки и не является букмекером. "
        "Это вероятности по истории матчей, решения вы принимаете сами.\n\n"
        "<b>Вы гарантируете выигрыш?</b>\n"
        "Нет. 70% — это не «всегда». Любой исход возможен.\n\n"
        "<b>Когда приходят прогнозы?</b>\n"
        "За 5 часов до начала матча, автоматически.\n\n"
        "<b>Сколько стоит?</b>\n"
        "7 дней — 149 ₽, 30 дней — 499 ₽, 90 дней — 1199 ₽. "
        "Первые 3 дня бесплатно. Автопродления нет.\n\n"
        "<b>Как отменить?</b>\n"
        "Ничего отменять не нужно: подписка не продлевается сама.\n\n"
        "<b>Как считается точность?</b>\n"
        "Модель обучается до начала окна и проверяется на матчах внутри окна. "
        "Публикуем и общую цифру, и только уверенные прогнозы.",
        reply_markup=main_menu() if has_access else None,
    )


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
async def handle_tariffs_button(callback: CallbackQuery, pool: asyncpg.Pool | None = None) -> None:
    await _event(pool, callback.from_user.id, "tariff_view")
    if callback.message:
        await callback.message.answer(tariffs_text(), reply_markup=tariffs_menu())
    await callback.answer()


@router.callback_query(F.data.startswith("buy:"))
async def handle_buy(callback: CallbackQuery, pool: asyncpg.Pool | None = None) -> None:
    await _event(pool, callback.from_user.id, "buy_click")
    days = int((callback.data or "buy:30").split(":")[1])
    days, price = tariff(days)
    if callback.message:
        await callback.message.answer(order_text(days, price), reply_markup=order_menu(days, price))
    await callback.answer()


@router.callback_query(F.data.startswith("pay:"))
async def handle_pay(callback: CallbackQuery, pool: asyncpg.Pool | None = None) -> None:
    await _event(pool, callback.from_user.id, "pay_click")
    days = int((callback.data or "pay:30").split(":")[1])
    days, price = tariff(days)
    if not isinstance(callback.message, Message):
        await callback.answer()
        return

    settings = get_settings()
    if pool is None or not settings.yookassa_configured:
        await callback.message.answer(
            "💳 <b>Оплата %d ₽</b>\n\n"
            "Платёжный сервис подключается. Пока могу выдать доступ вручную: "
            "напишите @khid71." % price,
            reply_markup=order_menu(days, price),
        )
        await callback.answer()
        return

    try:
        row_id = await db.create_payment(pool, callback.from_user.id, days, price)
        async with aiohttp.ClientSession() as session:
            payment = await create_payment(
                session,
                shop_id=settings.yookassa_shop_id or "",
                secret_key=settings.yookassa_secret_key or "",
                amount_rub=price,
                description="Подписка Football Analyzer, %d дней" % days,
                return_url=settings.yookassa_return_url or "https://t.me/footballizator_bot",
                chat_id=callback.from_user.id,
                days=days,
            )
        await db.attach_payment_id(pool, row_id, str(payment["id"]))
        url = (payment.get("confirmation") or {}).get("confirmation_url")
        if not url:
            raise RuntimeError("ЮKassa не вернула ссылку на оплату")
    except Exception as exc:  # noqa: BLE001
        log.error("не удалось создать платёж: %s", exc)
        await callback.message.answer(
            "Не получилось создать платёж. Попробуйте ещё раз через минуту или напишите @khid71."
        )
        await callback.answer("Ошибка платежа", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Перейти к оплате %d ₽" % price, url=url)
    builder.button(text="⬅️ К тарифам", callback_data=CALLBACK_TARIFFS)
    builder.adjust(1)
    await callback.message.answer(
        "🧾 <b>Заказ создан</b>\n\n"
        "Срок: <b>%d дней</b>\nЦена: <b>%d ₽</b>\n\n"
        "Нажмите «Перейти к оплате» — откроется страница ЮKassa. "
        "Доступ включится автоматически в течение пары минут после оплаты." % (days, price),
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


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
