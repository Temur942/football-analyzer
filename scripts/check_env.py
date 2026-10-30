#!/usr/bin/env python3
"""check_env.py — проверка окружения перед запуском бота.

Бесплатно и без сети: скрипт ничего не скачивает и никуда не стучится.
Отвечает на вопрос «можно ли вообще запускать то, что лежит в репозитории».

Что проверяет
-------------
  1. Версию Python (нужна 3.11+)
  2. Наличие обязательных зависимостей из pyproject.toml
  3. Наличие .env.local и заполненность BOT_TOKEN
  4. Записываемость каталога var/ (там SQLite и реестр расходов)
  5. Наличие config/prices.json

Коды выхода: 0 — можно запускать, 1 — есть критические проблемы.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parents[1]
MIN_PYTHON = (3, 11)

REQUIRED_MODULES = {
    "aiogram": "Telegram-бот",
    "apscheduler": "планировщик",
    "scipy": "математика прогноза",
    "soccerdata": "футбольная статистика",
    "aiosqlite": "база данных",
    "aiohttp": "HTTP-запросы",
    "pydantic": "конфиги и модели",
    "pydantic_settings": "настройки из окружения",
    "dotenv": "загрузка .env",
}


def check_python() -> Tuple[bool, str]:
    version = "%d.%d.%d" % sys.version_info[:3]
    if sys.version_info[:2] >= MIN_PYTHON:
        return True, "Python %s — подходит (нужно %d.%d+)" % (version, *MIN_PYTHON)
    return False, "Python %s — НЕ подходит, нужно %d.%d+" % (version, *MIN_PYTHON)


def check_modules() -> List[Tuple[bool, str]]:
    results: List[Tuple[bool, str]] = []
    for module, purpose in REQUIRED_MODULES.items():
        found = importlib.util.find_spec(module) is not None
        if found:
            results.append((True, "%s — установлен (%s)" % (module, purpose)))
        else:
            results.append((False, "%s — НЕ установлен (%s)" % (module, purpose)))
    return results


def check_env_file() -> List[Tuple[bool, str]]:
    results: List[Tuple[bool, str]] = []
    env_local = ROOT / ".env.local"
    if env_local.is_file():
        results.append((True, ".env.local — найден"))
        token = ""
        for line in env_local.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("BOT_TOKEN="):
                token = line.split("=", 1)[1].strip()
        if token:
            results.append((True, "BOT_TOKEN — заполнен (значение не показываю)"))
        else:
            results.append((False, "BOT_TOKEN — пуст: бот не стартует"))
    else:
        results.append((False, ".env.local — не найден: скопируйте из .env.example"))
    return results


def check_var_dir() -> Tuple[bool, str]:
    var_dir = ROOT / "var"
    try:
        var_dir.mkdir(parents=True, exist_ok=True)
        probe = var_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return False, "каталог var/ — недоступен для записи: %s" % exc
    return True, "каталог var/ — доступен для записи"


def check_prices() -> Tuple[bool, str]:
    path = Path(os.environ.get("COST_PRICES") or (ROOT / "config" / "prices.json"))
    if not path.is_file():
        return False, "config/prices.json — не найден: учёт расходов не заработает"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, "config/prices.json — битый JSON: %s" % exc
    operations = data.get("operations") or {}
    return True, "config/prices.json — валиден, платных операций описано: %d" % len(operations)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка окружения football-analyzer")
    parser.add_argument("--json", action="store_true", help="вывести результат в JSON")
    args = parser.parse_args(argv)

    python_ok, python_msg = check_python()
    var_ok, var_msg = check_var_dir()
    prices_ok, prices_msg = check_prices()

    checks: List[Tuple[bool, str]] = [(python_ok, python_msg)]
    checks.extend(check_env_file())
    checks.append((var_ok, var_msg))
    checks.append((prices_ok, prices_msg))
    module_checks = check_modules()
    checks.extend(module_checks)

    problems = [message for ok, message in checks if not ok]
    ready = not problems

    if args.json:
        print(json.dumps({"ready": ready, "problems": problems}, ensure_ascii=False, indent=2))
        return 0 if ready else 1

    print("=" * 66)
    print("  ПРОВЕРКА ОКРУЖЕНИЯ football-analyzer")
    print("=" * 66)
    for ok, message in checks:
        print("  %s %s" % ("[ok]  " if ok else "[НЕТ] ", message))
    print("=" * 66)
    if ready:
        print("  ИТОГ: окружение готово к запуску.")
        return 0
    print("  ИТОГ: проблем %d — запускать нельзя." % len(problems))
    return 1


if __name__ == "__main__":
    sys.exit(main())
