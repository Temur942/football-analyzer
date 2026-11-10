"""Публикация в ВК: сообщество + wall.post.

Telegram-тексты содержат HTML-теги, а ВК их не понимает — перед отправкой
теги снимаем, а ссылки оставляем текстом.
"""

from __future__ import annotations

import logging
import re

import aiohttp

log = logging.getLogger(__name__)

API_URL = "https://api.vk.com/method/wall.post"
API_VERSION = "5.199"


def plain_text(html_text: str) -> str:
    """Убрать HTML-разметку, оставив читаемый текст."""
    text = re.sub(r"</?(?:b|i|u|s|code|pre)>", "", html_text)
    text = re.sub(r"<a[^>]*>(.*?)</a>", r"\1", text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return text.strip()


async def post_to_vk(
    session: aiohttp.ClientSession,
    *,
    token: str,
    group_id: str,
    message: str,
) -> int:
    """Опубликовать запись на стене сообщества. Возвращает id записи."""
    payload = {
        "owner_id": "-%s" % group_id.lstrip("-"),
        "from_group": 1,
        "message": plain_text(message),
        "access_token": token,
        "v": API_VERSION,
    }
    async with session.post(API_URL, data=payload) as response:
        data = await response.json(content_type=None)
    if "error" in data:
        error = data["error"]
        raise RuntimeError("ВК: %s (%s)" % (error.get("error_msg"), error.get("error_code")))
    return int((data.get("response") or {}).get("post_id") or 0)
