"""Хендлеры aiogram: команды бота.

Статус: минимальный набор. Рассылка прогнозов появится в scheduler/.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from football_analyzer.config import get_settings

router = Router(name="basic")


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    settings = get_settings()
    await message.answer(
        "Привет! Это football-analyzer.\n\n"
        "Я слежу за расписанием матчей и присылаю математические прогнозы "
        "за %d ч до начала игры." % settings.forecast_lead_hours
    )


@router.message(Command("help"))
async def handle_help(message: Message) -> None:
    await message.answer(
        "Доступные команды:\n"
        "/start — начать\n"
        "/help — эта справка\n\n"
        "Список лиг и формат прогноза уточняются."
    )
