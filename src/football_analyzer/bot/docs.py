"""Выдача юридических документов прямо в чате.

Документы лежат в репозитории (docs/legal) — единый источник правды: то же, что
отдаётся пользователю, можно опубликовать на сайте. Пересобирать текст в коде
нельзя: расхождение бота и сайта — юридическая проблема.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from aiogram.types import Message

from football_analyzer.config import PROJECT_ROOT

log = logging.getLogger(__name__)

LEGAL_DIR = PROJECT_ROOT / "docs" / "legal"

# Ключ → (заголовок для кнопки, файл)
DOCUMENTS: dict[str, tuple[str, str]] = {
    "offer": ("📄 Публичная оферта", "offer.md"),
    "terms": ("📄 Пользовательское соглашение", "terms.md"),
    "privacy": ("📄 Политика ПД", "privacy.md"),
}

CHUNK_LIMIT = 3500


def _strip_markdown(text: str) -> str:
    """Привести markdown к читаемому тексту без HTML-разметки.

    Отправляем без parse_mode: иначе символы < и & в тексте сломают сообщение.
    """
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = line.replace("**", "").replace("__", "")
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if all(set(cell) <= {"-", ":", " "} and cell for cell in cells):
                continue  # разделитель таблицы
            line = " · ".join(cell for cell in cells if cell)
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def split_text(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """Разбить на части по абзацам: Telegram не принимает больше 4096 символов."""
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = (current + "\n\n" + paragraph).strip() if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > limit:
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


def load_document(key: str) -> str:
    title, filename = DOCUMENTS[key]
    path: Path = LEGAL_DIR / filename
    if not path.is_file():
        raise FileNotFoundError("не найден документ %s" % path)
    return _strip_markdown(path.read_text(encoding="utf-8"))


async def send_document(message: Message, key: str) -> list[int]:
    """Отправить документ частями. Возвращает id сообщений — чтобы потом удалить."""
    text = load_document(key)
    ids: list[int] = []
    for chunk in split_text(text):
        sent = await message.answer(chunk, parse_mode=None)
        ids.append(sent.message_id)
    return ids


async def delete_messages(bot: object, chat_id: int, message_ids: list[int]) -> None:
    """Удалить сообщения документа. Ошибки игнорируем: старое Telegram не отдаст."""
    for message_id in message_ids:
        try:
            await bot.delete_message(chat_id, message_id)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            continue
