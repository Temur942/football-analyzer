"""ЮKassa: создание платежа и проверка статуса.

Используем только два метода API: создание платежа и чтение его статуса.
Webhook не нужен: бот сам опрашивает незакрытые платежи раз в две минуты,
поэтому оплата не потеряется, даже если ответ пришёл позже или сервер
перезапускался.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import aiohttp
from aiohttp import BasicAuth

log = logging.getLogger(__name__)

API_URL = "https://api.yookassa.ru/v3/payments"


class YooKassaError(RuntimeError):
    """Ошибка платёжного сервиса с понятным текстом для лога."""


def _auth(shop_id: str, secret_key: str) -> BasicAuth:
    return BasicAuth(shop_id, secret_key, encoding="utf-8")


async def create_payment(
    session: aiohttp.ClientSession,
    *,
    shop_id: str,
    secret_key: str,
    amount_rub: int,
    description: str,
    return_url: str,
    chat_id: int,
    days: int,
) -> dict[str, Any]:
    """Создать платёж. Возвращает ответ ЮKassa (id и confirmation_url)."""
    payload = {
        "amount": {"value": "%d.00" % amount_rub, "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": description,
        "metadata": {"chat_id": str(chat_id), "days": str(days)},
    }
    headers = {"Idempotence-Key": str(uuid.uuid4()), "Content-Type": "application/json"}
    async with session.post(
        API_URL, json=payload, headers=headers, auth=_auth(shop_id, secret_key)
    ) as response:
        data = await response.json(content_type=None)
        if response.status >= 300:
            raise YooKassaError(
                "создание платежа: HTTP %d, %s" % (response.status, data.get("description") or data)
            )
        return data


async def fetch_payment(
    session: aiohttp.ClientSession,
    *,
    shop_id: str,
    secret_key: str,
    payment_id: str,
) -> dict[str, Any]:
    """Узнать текущий статус платежа."""
    async with session.get(
        "%s/%s" % (API_URL, payment_id), auth=_auth(shop_id, secret_key)
    ) as response:
        data = await response.json(content_type=None)
        if response.status >= 300:
            raise YooKassaError(
                "статус платежа: HTTP %d, %s" % (response.status, data.get("description") or data)
            )
        return data
