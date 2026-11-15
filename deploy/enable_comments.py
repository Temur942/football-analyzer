"""Разовое включение комментариев на сервере: nginx + миграция + служба.

Скрипт идемпотентный: повторный запуск ничего не ломает.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

CONF = Path("/etc/nginx/sites-available/football-landing")

BLOCK = """    location /api/ {
        proxy_pass http://127.0.0.1:8010;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location = /auth/telegram {
        proxy_pass http://127.0.0.1:8010;
        proxy_set_header Host $host;
    }

    location / {"""


def run(command: str) -> None:
    print("+", command)
    subprocess.run(command, shell=True, check=False)


def main() -> None:
    text = CONF.read_text(encoding="utf-8")
    if "location /api/" not in text:
        if "location / {" in text:
            text = text.replace("    location / {", BLOCK, 1)
        else:
            text = text.replace("location / {", BLOCK, 1)
        CONF.write_text(text, encoding="utf-8")
        print("nginx: прокси добавлен")
    else:
        print("nginx: уже настроен")

    run("nginx -t")
    run("systemctl reload nginx")
    run("systemctl daemon-reload")
    run("systemctl enable --now football-comments.service")
    run("sleep 4")
    run("systemctl is-active football-comments.service")
    run("curl -s -m 10 'http://127.0.0.1:8010/api/comments?subject=match:1'")


if __name__ == "__main__":
    main()
