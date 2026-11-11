"""Публикация в ВК: сообщество + wall.post.

Telegram-тексты содержат HTML-теги, а ВК их не понимает — перед отправкой
теги снимаем, а ссылки оставляем текстом.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

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


async def upload_wall_photo(
    session: aiohttp.ClientSession,
    *,
    token: str,
    group_id: str,
    image_path: Path,
) -> str:
    """Загрузить картинку в ВК и получить attachment для записи.

    Три шага по документации ВК: получить адрес загрузки, отправить файл,
    сохранить фото на стене сообщества.
    """
    params = {
        "group_id": group_id.lstrip("-"),
        "access_token": token,
        "v": API_VERSION,
    }
    async with session.get(
        "https://api.vk.com/method/photos.getWallUploadServer", params=params
    ) as response:
        data = await response.json(content_type=None)
    if "error" in data:
        raise RuntimeError("ВК загрузка: %s" % data["error"].get("error_msg"))
    upload_url = data["response"]["upload_url"]

    form = aiohttp.FormData()
    with image_path.open("rb") as handle:
        form.add_field("photo", handle, filename=image_path.name, content_type="image/png")
        async with session.post(upload_url, data=form) as response:
            uploaded = await response.json(content_type=None)

    save_params = {
        "group_id": group_id.lstrip("-"),
        "server": uploaded.get("server"),
        "photo": uploaded.get("photo"),
        "hash": uploaded.get("hash"),
        "access_token": token,
        "v": API_VERSION,
    }
    async with session.get(
        "https://api.vk.com/method/photos.saveWallPhoto", params=save_params
    ) as response:
        saved = await response.json(content_type=None)
    if "error" in saved:
        raise RuntimeError("ВК сохранение фото: %s" % saved["error"].get("error_msg"))
    photo = saved["response"][0]
    return "photo%s_%s" % (photo["owner_id"], photo["id"])


async def post_to_vk(
    session: aiohttp.ClientSession,
    *,
    token: str,
    group_id: str,
    message: str,
    attachment: str | None = None,
) -> int:
    """Опубликовать запись на стене сообщества. Возвращает id записи."""
    payload = {
        "owner_id": "-%s" % group_id.lstrip("-"),
        "from_group": 1,
        "message": plain_text(message),
        "access_token": token,
        "v": API_VERSION,
    }
    if attachment:
        payload["attachments"] = attachment
    async with session.post(API_URL, data=payload) as response:
        data = await response.json(content_type=None)
    if "error" in data:
        error = data["error"]
        raise RuntimeError("ВК: %s (%s)" % (error.get("error_msg"), error.get("error_code")))
    return int((data.get("response") or {}).get("post_id") or 0)
