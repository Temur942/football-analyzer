"""Комментарии к матчам: небольшой сервис за nginx.

Авторизация — Telegram Login Widget: браузер присылает подписанные данные,
сервис проверяет подпись ключом бота и выдаёт cookie. Писать могут только
вошедшие, читать — все.

Почему отдельный процесс, а не бот: бот занят Telegram-поллингом, а падение
сайта не должно ронять доставку прогнозов.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import logging
import time

from aiohttp import web

from football_analyzer.config import get_settings
from football_analyzer.storage import db

log = logging.getLogger("comments")

MAX_LENGTH = 500
MIN_INTERVAL = 20.0
COOKIE = "fa_user"


def verify_telegram(data: dict[str, str], bot_token: str) -> bool:
    """Проверить подпись данных Telegram Login Widget."""
    received = data.get("hash", "")
    if not received:
        return False
    check = "\n".join(
        "%s=%s" % (key, value)
        for key, value in sorted(data.items())
        if key not in ("hash", "fa_user")
    )
    secret = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


def sign(chat_id: int, secret: str) -> str:
    digest = hmac.new(secret.encode(), str(chat_id).encode(), hashlib.sha256).hexdigest()
    return "%s.%s" % (chat_id, digest[:32])


def unsign(value: str, secret: str) -> int | None:
    try:
        chat_id_text, signature = value.split(".", 1)
        chat_id = int(chat_id_text)
    except ValueError:
        return None
    expected = sign(chat_id, secret)[-32:]
    return chat_id if hmac.compare_digest(expected, signature) else None


class Comments:
    def __init__(self, pool, secret: str) -> None:
        self.pool = pool
        self.secret = secret
        self.bot_token = secret
        self.last_post: dict[int, float] = {}

    def user(self, request: web.Request) -> int | None:
        return unsign(request.cookies.get(COOKIE, ""), self.secret)

    async def list_comments(self, request: web.Request) -> web.Response:
        subject = request.query.get("subject", "")[:64]
        if not subject:
            return web.json_response({"error": "subject"}, status=400)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT chat_id, display_name, body, created_at FROM comments"
                " WHERE subject = $1 AND NOT hidden ORDER BY created_at DESC LIMIT 50",
                subject,
            )
        return web.json_response(
            {
                "items": [
                    {
                        "name": row["display_name"] or "болельщик",
                        "body": row["body"],
                        "at": row["created_at"].isoformat(),
                        "mine": row["chat_id"] == self.user(request),
                    }
                    for row in rows
                ]
            }
        )

    async def add_comment(self, request: web.Request) -> web.Response:
        chat_id = self.user(request)
        if chat_id is None:
            return web.json_response({"error": "auth"}, status=401)
        payload = await request.json()
        subject = str(payload.get("subject", ""))[:64]
        body = html.escape(str(payload.get("body", "")).strip())[:MAX_LENGTH]
        if not subject or len(body) < 2:
            return web.json_response({"error": "empty"}, status=400)
        now = time.monotonic()
        if now - self.last_post.get(chat_id, 0) < MIN_INTERVAL:
            return web.json_response({"error": "slow", "retry": MIN_INTERVAL}, status=429)
        self.last_post[chat_id] = now
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO comments (subject, chat_id, display_name, body)"
                " VALUES ($1, $2, $3, $4)",
                subject,
                chat_id,
                payload.get("name") or None,
                body,
            )
        return web.json_response({"ok": True})

    async def auth(self, request: web.Request) -> web.Response:
        data = dict(request.query)
        if not verify_telegram(data, self.bot_token):
            return web.Response(text="Не удалось подтвердить вход", status=403)
        chat_id = int(data["id"])
        response = web.HTTPFound("/")
        response.headers["Cache-Control"] = "no-store"
        response.set_cookie(
            COOKIE,
            sign(chat_id, self.secret),
            max_age=60 * 60 * 24 * 90,
            httponly=True,
            samesite="Lax",
        )
        return response

    async def me(self, request: web.Request) -> web.Response:
        return web.json_response({"chat_id": self.user(request)})


async def main() -> None:
    settings = get_settings()
    token = settings.bot_token or ""
    if not token:
        raise SystemExit("нет BOT_TOKEN")
    pool = await db.connect(settings.require_database_url())
    app = web.Application()
    service = Comments(pool, token)
    app["service"] = service
    app.router.add_get("/api/comments", service.list_comments)
    app.router.add_post("/api/comments", service.add_comment)
    app.router.add_get("/api/me", service.me)
    app.router.add_get("/auth/telegram", service.auth)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8010)
    await site.start()
    log.info("комментарии слушают 127.0.0.1:8010")
    # Сервис живёт, пока его не выключат.
    await asyncio.Event().wait()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
